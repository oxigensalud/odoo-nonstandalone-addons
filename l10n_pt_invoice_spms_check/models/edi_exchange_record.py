# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import logging
import re

import requests
from lxml import etree
from zeep import Client, Settings
from zeep.transports import Transport
from zeep.wsse.username import UsernameToken

from odoo import models
from odoo.modules.module import get_resource_path

_logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60


class EdiExchangeRecord(models.Model):
    _inherit = "edi.exchange.record"

    def _cron_l10n_pt_spms_check_update(self):
        """Fetch the conference check result of every sent invoice.

        FACE-style polling (precedent: l10n_es_facturae_face): the
        queue is our own sent exchange records whose invoice has no
        definitive check result yet.
        """
        backend = self.env.ref("l10n_pt_invoice_spms.spms_backend")
        send_type = self.env.ref("l10n_pt_invoice_spms.spms_exchange_type")
        exchanges = self.search(
            [
                ("backend_id", "=", backend.id),
                ("type_id", "=", send_type.id),
                (
                    "edi_exchange_state",
                    "in",
                    ["output_sent_and_processed", "output_sent"],
                ),
                ("model", "=", "account.move"),
            ]
        )
        clients = {}
        for exchange in exchanges:
            move = exchange.record
            if not move or move.state != "posted":
                continue
            if any(move.spms_invoice_check_ids.mapped("check_state")):
                continue
            if self.search_count(
                [
                    ("type_id.code", "=", "l10n_pt_spms_check"),
                    ("model", "=", "account.move"),
                    ("res_id", "=", move.id),
                    (
                        "edi_exchange_state",
                        "in",
                        [
                            "input_received",
                            "input_processed",
                            "input_processed_error",
                        ],
                    ),
                ]
            ):
                continue
            client = clients.get(move.company_id.id)
            if client is None:
                client = clients[move.company_id.id] = self._l10n_pt_spms_check_client(
                    move.company_id
                )
            try:
                self._l10n_pt_spms_check_poll(backend, client, exchange, move)
            except Exception:
                _logger.exception("SPMS check of %s: unexpected failure", move.name)

    def _l10n_pt_spms_check_client(self, company):
        """One zeep client per company and pass: the WSSE token carries
        that company's portal credentials.

        The shipped WSDL is the live one with its three read-path
        defects fixed (see api/FacturaCRDWS.wsdl): the live file
        declares the request without any parameter, omits the
        <return> wrapper of the response and points to an internal
        host — zeep against the raw live WSDL cannot work.
        """
        wsdl = get_resource_path(
            "l10n_pt_invoice_spms_check", "api", "FacturaCRDWS.wsdl"
        )
        return Client(
            wsdl,
            wsse=UsernameToken(company.spms_username, company.spms_password),
            transport=Transport(
                timeout=REQUEST_TIMEOUT, operation_timeout=REQUEST_TIMEOUT
            ),
            settings=Settings(raw_response=True),
        )

    def _l10n_pt_spms_check_poll(self, backend, client, exchange, move):
        """Fetch and route the CCF answer for one sent invoice."""
        try:
            code, document = self._l10n_pt_spms_check_fetch(client, move)
        except (requests.RequestException, etree.XMLSyntaxError) as err:
            _logger.warning("SPMS check of %s: request failed (%s)", move.name, err)
            return
        if document:
            try:
                with self.env.cr.savepoint():
                    child = backend.create_record(
                        "l10n_pt_spms_check",
                        {
                            "edi_exchange_state": "input_received",
                            "model": exchange.model,
                            "res_id": exchange.res_id,
                            "parent_id": exchange.id,
                        },
                    )
                    child._set_file_content(document)
            except Exception:
                _logger.exception(
                    "SPMS check of %s: could not store the result", move.name
                )
                return
            try:
                with self.env.cr.savepoint():
                    backend.exchange_process(child)
            except Exception:
                _logger.exception(
                    "SPMS check of %s: result stored but not processed",
                    move.name,
                )
        elif code == "301":
            with self.env.cr.savepoint():
                self._l10n_pt_spms_check_flag_incident(move, code)
        elif code in ("302", "999"):
            _logger.debug("SPMS check of %s: no result yet (code %s)", move.name, code)
        else:
            _logger.warning(
                "SPMS check of %s: unrecognised answer (return code %s)",
                move.name,
                code,
            )

    def _l10n_pt_spms_check_flag_incident(self, move, code):
        """Store only the code: the message is composed — and
        translated — when the result is read."""
        result = move.spms_invoice_check_ids[:1]
        if not result:
            self.env["spms.invoice.check"].create(
                {"move_id": move.id, "ws_incident_code": code}
            )
        elif result.ws_incident_code != code:
            result.ws_incident_code = code

    def _l10n_pt_spms_check_fetch(self, client, move):
        """Ask the CCF for the check result of one invoice.

        The request mirrors the proven submission envelope of
        l10n_pt_invoice_spms: the identification travels wrapped in a
        <factura> element and must carry codigoPrestador and
        dataFactura — the CCF resolves the invoice by (numeroFactura,
        dataFactura), so a poll without the date answers 301 for
        every invoice, issued or not.
        """
        vat = move.company_id.vat
        if vat and len(vat) >= 11:
            vat = vat[-9:]
        response = client.service.obterResultadoConferencia(
            factura={
                "areaConferencia": 3,
                "codigoPrestador": move.partner_id.sudo().spms_assigned_id,
                "dataFactura": move.invoice_date,
                "nif": int(vat) if vat and vat.isdigit() else None,
                "numeroFactura": move._get_spms_invoice_number(),
            }
        )
        return self._l10n_pt_spms_check_parse_response(response.content)

    def _l10n_pt_spms_check_parse_response(self, content):
        """Split a WS answer into (return code, check document).

        Real service behaviour (probed July-August 2026): a result
        carries the base64 conference file in return/documento over
        HTTP 200, while "factura inexistente" (301) and "not yet
        conferred" (302) arrive as SOAP faults over HTTP 500 whose
        faultstring starts with the code ("301 - Factura
        Inexistente."). Fields are still located tolerantly by local
        name — the WSDL keeps the response types private — and a
        base64 document is returned as raw bytes: the encoding its
        XML declaration announces must survive verbatim into the
        stored file.
        """
        parser = etree.XMLParser(resolve_entities=False)
        root = etree.fromstring(content, parser)
        code = None
        document = None
        for element in root.iter():
            if not isinstance(element.tag, str):
                continue
            tag = etree.QName(element).localname
            if tag == "documento" and element.text and element.text.strip():
                document = element.text.strip()
            elif tag == "faultstring" and element.text:
                match = re.match(r"\s*(\d{3})", element.text)
                if match:
                    code = match.group(1)
            elif code is None and tag in (
                "codigoRetorno",
                "codigoResposta",
                "codigo",
            ):
                code = (element.text or "").strip()
        if document:
            try:
                decoded = base64.b64decode(document, validate=True)
                etree.fromstring(decoded, parser)
            except (ValueError, etree.XMLSyntaxError):
                pass
            else:
                document = decoded
        return code, document
