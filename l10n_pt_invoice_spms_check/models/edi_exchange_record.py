# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re
import traceback

import requests
from zeep import Client
from zeep.exceptions import Error as ZeepError, Fault
from zeep.transports import Transport
from zeep.wsse.username import UsernameToken

from odoo import _, api, models
from odoo.modules.module import get_resource_path

REQUEST_TIMEOUT = 60


class EdiExchangeRecord(models.Model):
    _inherit = "edi.exchange.record"

    @api.depends("type_id.ack_type_id", "model", "res_id")
    def _compute_ack_expected(self):
        """A sent invoice expects its check result as its ACK; a sent
        credit or debit note expects nothing.

        The sending type of l10n_pt_invoice_spms carries invoices and
        notes alike and names our check result type as its ACK type,
        so edi_oca would expect an answer on every sent record. The
        CCF checks invoices only: the expectation is narrowed to them
        here, and the form of a sent note shows no ACK group.
        """
        for rec in self:
            # edi_oca reads the ACK type of the whole recordset at once:
            # computed one record at a time, so a batch never inherits
            # the expectation of another type
            super(EdiExchangeRecord, rec)._compute_ack_expected()
            if (
                rec.ack_expected
                and rec.type_id.ack_type_id.code == "l10n_pt_spms_check"
            ):
                rec.ack_expected = rec._l10n_pt_spms_check_checkable()

    def _l10n_pt_spms_check_checkable(self):
        """Whether the related record is a customer invoice, the only
        document the CCF checks (the sending type also carries notes)."""
        self.ensure_one()
        move = self.record
        return (
            bool(move)
            and move._name == "account.move"
            and move.move_type == "out_invoice"
        )

    def _cron_l10n_pt_spms_check_update(self):
        """Expect, then ask the CCF for, the check result of every sent
        invoice.

        FACE-style polling (precedent: l10n_es_facturae_face) on the
        answer edi_oca already models: the check result is the ACK the
        sent invoice expects. The first pass after the sending creates
        the result record, waiting, as the child of the sent record;
        from then on the waiting result records are the queue. The
        scheduled action only queues one job per record, the way
        edi_oca dispatches its own exchanges: every call to the CCF
        runs in its own transaction, so a long backlog is never undone
        as a whole by the cron time limit. A poll that ran out of
        retries is re-activated, not replaced: a result never has more
        than one poll job.
        """
        backend = self.env.ref("l10n_pt_invoice_spms.spms_backend")
        send_type = self.env.ref("l10n_pt_invoice_spms.spms_exchange_type")
        sent = self.search(
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
        for exchange in sent:
            if exchange._l10n_pt_spms_check_expected():
                exchange.exchange_create_ack_record(edi_exchange_state="input_pending")
        failed_polls = self._l10n_pt_spms_check_failed_polls()
        results = self.search(
            [
                ("backend_id", "=", backend.id),
                ("type_id.code", "=", "l10n_pt_spms_check"),
                (
                    "edi_exchange_state",
                    "in",
                    ["input_pending", "input_receive_error"],
                ),
                ("model", "=", "account.move"),
            ]
        )
        for result in results:
            if not result._l10n_pt_spms_check_open():
                continue
            failed = failed_polls.get(result.id)
            if failed:
                failed.requeue()
            else:
                result.with_delay().action_l10n_pt_spms_check_poll()

    def _l10n_pt_spms_check_failed_polls(self):
        """The poll jobs that ran out of retries, by result record.

        A failed job is final for queue_job: nothing re-runs it, and
        the identity key of a poll job only stops duplicates of a live
        one, so the next pass would queue a second poll of the same
        result and leave the failed one behind. The scheduled action
        re-activates that job instead — what the Requeue button does —
        so a result keeps one poll job whatever the CCF answers.
        """
        jobs = self.env["queue.job"].search(
            [
                ("model_name", "=", self._name),
                ("method_name", "=", "action_l10n_pt_spms_check_poll"),
                ("state", "=", "failed"),
            ]
        )
        by_result = {}
        for job in jobs:
            if len(job.records) != 1:
                # not queued by the scheduled action, which polls one
                # result per job
                continue
            key = job.records.id
            by_result[key] = by_result.get(key, jobs.browse()) | job
        return by_result

    def _l10n_pt_spms_check_expected(self):
        """Whether this sent record still owes its result record.

        A posted invoice with no definitive check result and no ACK
        yet: an existing check child, whatever its state, is the ACK,
        so the first pass backfills every already-sent invoice without
        duplicating anything.
        """
        self.ensure_one()
        if not self._l10n_pt_spms_check_checkable():
            return False
        move = self.record
        return (
            move.state == "posted"
            and not any(move.spms_invoice_check_ids.mapped("check_state"))
            and bool(self.needs_ack())
        )

    def _l10n_pt_spms_check_open(self):
        """Whether this result record is still to be asked for: waiting
        or in error on reception, its invoice posted and not settled."""
        self.ensure_one()
        if self.edi_exchange_state not in ("input_pending", "input_receive_error"):
            return False
        move = self.record
        return (
            bool(move)
            and move.state == "posted"
            and not any(move.spms_invoice_check_ids.mapped("check_state"))
        )

    def action_l10n_pt_spms_check_poll(self):
        """Ask the CCF for the check result this record waits for.

        One queue job per result record. Every answer of the CCF —
        a transport failure included — is written on the record, where
        the user looks; the job fails only on a software failure.
        """
        self.ensure_one()
        if not self._l10n_pt_spms_check_open():
            # settled between queueing and running
            return
        move = self.record
        client = self._l10n_pt_spms_check_client(move.company_id)
        self._l10n_pt_spms_check_poll(client, move)

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

    def _l10n_pt_spms_check_poll(self, client, move):
        """Fetch the CCF answer for one waiting result and write it on
        the record: a document is received and processed, "not checked
        yet" keeps it waiting, anything else is an error on reception
        with its reason, asked again at the next pass."""
        try:
            code, document, answer = self._l10n_pt_spms_check_fetch(client, move)
        except (requests.RequestException, ZeepError) as err:
            # transport trouble (a timeout, an outage page instead of
            # SOAP, an answer off the WSDL contract): no verdict of the
            # CCF, and the only error that keeps its traceback
            self._l10n_pt_spms_check_receive_error(
                _("The CCF web service could not be reached: %s") % err,
                traceback.format_exc(),
            )
            return
        if document:
            self._l10n_pt_spms_check_receive(document)
        elif code == "302":
            self._l10n_pt_spms_check_still_waiting()
        elif code == "301":
            self._l10n_pt_spms_check_receive_error(
                _(
                    "The CCF does not recognise invoice %(invoice)s even though "
                    "it was sent successfully (%(answer)s)."
                )
                % {"invoice": move.name, "answer": answer}
            )
        elif code == "999":
            # the CCF's own "service unavailable" answer, seen for weeks
            # at a time
            self._l10n_pt_spms_check_receive_error(
                _("The CCF web service is unavailable (%s).") % answer
            )
        elif code:
            self._l10n_pt_spms_check_receive_error(
                _("The CCF answered with an unexpected return code: %s.") % answer
            )
        else:
            self._l10n_pt_spms_check_receive_error(
                _("The CCF answered without a check document or a return code.")
            )

    def _l10n_pt_spms_check_receive(self, document):
        """Store the check document on the record and process it.

        The record is moved to received by hand — the generic receive
        of edi_oca has no "not yet" outcome — and edi_oca takes over
        from there: the state sets exchanged_on (the "ACK received on"
        of the sent record), one message goes to the invoice's chatter,
        and exchange_process holds a rejected document in error on the
        record, with the reason, for Retry. Anything else is a bug: the
        job fails and the transaction rolls the record back to waiting.
        """
        self._set_file_content(document)
        self.write(
            {
                "edi_exchange_state": "input_received",
                "exchange_error": False,
                "exchange_error_traceback": False,
            }
        )
        self.notify_action_complete(
            "receive", message=self._exchange_status_message("receive_ok")
        )
        self.backend_id.exchange_process(self)

    def _l10n_pt_spms_check_still_waiting(self):
        """Not checked yet (302): waiting, the last error cleared.

        A plain 302 on a clean waiting record writes nothing. Retry on
        a record in error moves it back to waiting but keeps the error
        text, so the state alone does not tell a clean record apart.
        """
        if self.edi_exchange_state != "input_pending" or self.exchange_error:
            self.write(
                {
                    "edi_exchange_state": "input_pending",
                    "exchange_error": False,
                    "exchange_error_traceback": False,
                }
            )

    def _l10n_pt_spms_check_receive_error(self, message, traceback_txt=False):
        """Error on reception with its reason: asked again next pass,
        cleared by the next "not checked yet" or by the document."""
        self.write(
            {
                "edi_exchange_state": "input_receive_error",
                "exchange_error": message,
                "exchange_error_traceback": traceback_txt,
            }
        )

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

        Returns (code, document, answer): the code and the full
        faultstring of a coded fault, or the document of a plain
        answer — None when the answer carries none.
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
            answer = str(fault)
            match = re.match(r"\s*(\d{3})", answer)
            if not match:
                raise
            return match.group(1), None, answer
        # zeep unwraps the single <return> element of the answer: the
        # result is the response type itself, or None without <return>
        document = result.documento if result is not None else None
        return None, document, None
