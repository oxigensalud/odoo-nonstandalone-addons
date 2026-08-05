# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import logging
from uuid import uuid4

import requests
from lxml import etree

from odoo import models

from odoo.addons.l10n_pt_invoice_spms.components.edi_output_send_l10n_pt_spms import (
    FACTURA_NS,
    SOAPENV_NS,
    WSDL,
    WSSE_NS,
    WSU_NS,
)

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
        for exchange in exchanges:
            move = exchange.record
            if not move or move.state != "posted":
                continue
            if any(move.spms_invoice_check_ids.mapped("check_state")):
                # the first definitive result closes the invoice forever
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
                # waiting, processed or failed: never fetch a duplicate
                continue
            try:
                self._l10n_pt_spms_check_poll(backend, exchange, move)
            except Exception:
                _logger.exception("SPMS check of %s: unexpected failure", move.name)

    def _l10n_pt_spms_check_poll(self, backend, exchange, move):
        """Fetch and route the CCF answer for one sent invoice."""
        try:
            code, document = self._l10n_pt_spms_check_fetch(move)
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
                # payload safe on the child; processing is retriable
                _logger.exception(
                    "SPMS check of %s: result stored but not processed",
                    move.name,
                )
        elif code == "301":
            with self.env.cr.savepoint():
                self._l10n_pt_spms_check_flag_anomaly(move, code)
        elif code in ("302", "999"):
            _logger.debug("SPMS check of %s: no result yet (code %s)", move.name, code)
        else:
            _logger.warning(
                "SPMS check of %s: unrecognised answer (return code %s)",
                move.name,
                code,
            )

    def _l10n_pt_spms_check_flag_anomaly(self, move, code):
        """Store only the code: the message is composed — and
        translated — when the result is read."""
        check = move.spms_invoice_check_ids[:1]
        if not check:
            self.env["spms.invoice.check"].create(
                {"move_id": move.id, "ws_anomaly_code": code}
            )
        elif check.ws_anomaly_code != code:
            check.ws_anomaly_code = code

    def _l10n_pt_spms_check_fetch(self, move):
        """Ask the CCF for the check result of one invoice, mirroring
        the WSSE envelope of the sending component."""
        vat = move.company_id.vat
        if vat and len(vat) >= 11:
            vat = vat[-9:]
        root = etree.Element(
            f"{{{SOAPENV_NS}}}Envelope",
            nsmap={"soapenv": SOAPENV_NS, "fac": FACTURA_NS},
        )
        header = etree.SubElement(root, f"{{{SOAPENV_NS}}}Header")
        security = etree.SubElement(
            header, f"{{{WSSE_NS}}}Security", nsmap={"wsse": WSSE_NS, "wsu": WSU_NS}
        )
        security.set(etree.QName(SOAPENV_NS, "mustUnderstand"), "1")
        username_token = etree.SubElement(security, f"{{{WSSE_NS}}}UsernameToken")
        username_token.set(etree.QName(WSU_NS, "Id"), f"UsernameToken-{uuid4()}")
        username = etree.SubElement(username_token, f"{{{WSSE_NS}}}Username")
        username.text = move.company_id.spms_username
        password = etree.SubElement(username_token, f"{{{WSSE_NS}}}Password")
        password.set(
            "Type",
            "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText",  # noqa: B950
        )
        password.text = move.company_id.spms_password
        body = etree.SubElement(root, f"{{{SOAPENV_NS}}}Body")
        action = etree.SubElement(body, f"{{{FACTURA_NS}}}obterResultadoConferencia")
        etree.SubElement(action, "areaConferencia").text = "3"
        etree.SubElement(action, "nif").text = vat
        etree.SubElement(action, "numeroFactura").text = move._get_spms_invoice_number()
        xml = etree.tostring(root, encoding="utf-8", xml_declaration=False)
        response = requests.post(
            WSDL,
            data=xml.decode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "obterResultadoConferencia",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return self._l10n_pt_spms_check_parse_response(response.content)

    def _l10n_pt_spms_check_parse_response(self, content):
        """Split a WS answer into (return code, check document).

        The WSDL keeps the response types private, so both fields are
        located tolerantly by local name — the single place to adjust
        if a live answer differs. A base64 document is returned as raw
        bytes: the encoding its XML declaration announces must survive
        verbatim into the stored file.
        """
        # resolve_entities off: XXE hardening on external data
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
