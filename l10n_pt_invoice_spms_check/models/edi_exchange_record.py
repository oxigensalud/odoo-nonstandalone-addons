# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
import re

import requests
from zeep import Client
from zeep.exceptions import Error as ZeepError, Fault
from zeep.transports import Transport
from zeep.wsse.username import UsernameToken

from odoo import models
from odoo.modules.module import get_resource_path

from odoo.addons.queue_job.exception import RetryableJobError

_logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60


class EdiExchangeRecord(models.Model):
    _inherit = "edi.exchange.record"

    def _cron_l10n_pt_spms_check_update(self):
        """Queue the conference check of every sent invoice.

        FACE-style polling (precedent: l10n_es_facturae_face): the
        queue is our own sent exchange records whose invoice has no
        definitive check result yet. The scheduled action only queues
        one job per invoice, the way edi_oca dispatches its own
        exchanges: every call to the CCF then runs in its own
        transaction, so a long backlog is never undone as a whole by
        the cron time limit, and a failed poll is retried on its own.
        A poll that ran out of retries is re-activated, not replaced:
        an invoice never has more than one poll job.
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
        failed_polls = self._l10n_pt_spms_check_failed_polls()
        for exchange in exchanges:
            if not exchange._l10n_pt_spms_check_pending():
                continue
            failed = failed_polls.get(exchange.id)
            if failed:
                failed.requeue()
            else:
                exchange.with_delay().action_l10n_pt_spms_check_poll()

    def _l10n_pt_spms_check_failed_polls(self):
        """The poll jobs that ran out of retries, by exchange record.

        A failed job is final for queue_job: nothing re-runs it, and
        the identity key of a poll job only stops duplicates of a live
        one, so the next pass would queue a second poll of the same
        invoice and leave the failed one behind. The scheduled action
        re-activates that job instead — what the Requeue button does —
        so an invoice keeps one poll job whatever the CCF answers.
        """
        jobs = self.env["queue.job"].search(
            [
                ("model_name", "=", self._name),
                ("method_name", "=", "action_l10n_pt_spms_check_poll"),
                ("state", "=", "failed"),
            ]
        )
        by_exchange = {}
        for job in jobs:
            if len(job.records) != 1:
                # not queued by the scheduled action, which polls one
                # invoice per job
                continue
            key = job.records.id
            by_exchange[key] = by_exchange.get(key, jobs.browse()) | job
        return by_exchange

    def _l10n_pt_spms_check_pending(self):
        """Whether this sent exchange still awaits its check result."""
        self.ensure_one()
        move = self.record
        # The sending type also carries credit notes; the CCF checks
        # invoices only, so a nota is never asked for a result.
        if not move or move.move_type != "out_invoice" or move.state != "posted":
            return False
        if any(move.spms_invoice_check_ids.mapped("check_state")):
            return False
        return not self.search_count(
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
        )

    def action_l10n_pt_spms_check_poll(self):
        """Ask the CCF for the check result of this sent invoice.

        One queue job per invoice. A transport failure — or the CCF
        answering that its service is unavailable (999) — raises
        RetryableJobError so queue_job retries the poll by itself; any
        other failure, or the retries running out, leaves the job
        failed, visible in the queue, and the next pass of the
        scheduled action re-activates it.
        """
        self.ensure_one()
        if not self._l10n_pt_spms_check_pending():
            # settled between queueing and running
            return
        move = self.record
        backend = self.env.ref("l10n_pt_invoice_spms.spms_backend")
        client = self._l10n_pt_spms_check_client(move.company_id)
        self._l10n_pt_spms_check_poll(backend, client, move)

    def _l10n_pt_spms_check_client(self, company):
        """One zeep client per job: the WSSE token carries the portal
        credentials of the invoice's company.

        The shipped WSDL is the live one with its read path fixed
        (see api/FacturaCRDWS.wsdl): the live file declares the
        request without any parameter, omits the <return> wrapper of
        the response, points to an internal host and types the base64
        conference file as a plain string — zeep against the raw live
        WSDL cannot work.
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
        )

    def _l10n_pt_spms_check_poll(self, backend, client, move):
        """Fetch and route the CCF answer for one sent invoice."""
        try:
            code, document = self._l10n_pt_spms_check_fetch(client, move)
        except (requests.RequestException, ZeepError) as err:
            # transport trouble (a timeout, an outage page instead of
            # SOAP, an answer off the WSDL contract): the job retries by
            # itself, no need to wait a pass
            raise RetryableJobError(
                f"SPMS check of {move.name}: request failed ({err})"
            ) from err
        if document:
            try:
                with self.env.cr.savepoint():
                    child = backend.create_record(
                        "l10n_pt_spms_check",
                        {
                            "edi_exchange_state": "input_received",
                            "model": self.model,
                            "res_id": self.res_id,
                            "parent_id": self.id,
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
        elif code == "302":
            _logger.debug("SPMS check of %s: no result yet", move.name)
        elif code == "999":
            # the CCF's own "service unavailable" answer, seen for weeks
            # at a time: retried like a timeout, and nothing is written
            # on the invoice — it is honestly not checked yet
            raise RetryableJobError(
                f"SPMS check of {move.name}: the CCF service is unavailable (999)"
            )
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

        The answer is read by zeep against the shipped WSDL. Real
        service behaviour (probed July-August 2026): a result carries
        the base64 conference file in return/documento over HTTP 200
        — typed base64Binary, so it arrives here already decoded, the
        bytes the CCF encoded (the encoding its XML declaration
        announces survives verbatim into the stored file); "factura
        inexistente" (301), "not yet conferred" (302) and the
        service's own outage answer (999) arrive as SOAP faults over
        HTTP 500 whose faultstring starts with the code ("301 -
        Factura Inexistente."). A fault carrying no code is no verdict
        of the CCF — an outage page, a refusal of the proxy — and is
        left to the caller as transport trouble.
        """
        vat = move.company_id.vat
        if vat and len(vat) >= 11:
            vat = vat[-9:]
        try:
            result = client.service.obterResultadoConferencia(
                factura={
                    "areaConferencia": 3,
                    "codigoPrestador": move.partner_id.sudo().spms_assigned_id,
                    "dataFactura": move.invoice_date,
                    "nif": int(vat) if vat and vat.isdigit() else None,
                    "numeroFactura": move._get_spms_invoice_number(),
                }
            )
        except Fault as fault:
            match = re.match(r"\s*(\d{3})", str(fault))
            if not match:
                raise
            return match.group(1), None
        # zeep unwraps the single <return> element of the answer: the
        # result is the response type itself, or None without <return>
        document = result.documento if result is not None else None
        return None, document
