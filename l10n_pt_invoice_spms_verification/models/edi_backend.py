# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models


class EdiBackend(models.Model):
    _inherit = "edi.backend"

    def _input_pending_records_domain(self, record_ids=None):
        """Leave the waiting verification results to the module's own polling.

        They have no receive component: the generic receive of edi_oca
        has no "not yet" outcome (None is received without a file, an
        exception is an error on reception, and either posts on the
        invoice at every attempt), so its hourly "EDI exchange check
        input sync" would queue action_exchange_receive() on every
        waiting result — a NotImplementedError, a red job per record
        and per hour. The scheduled action of the module asks the CCF
        instead. The process step (input_received) stays with edi_oca
        as a safety net.
        """
        domain = super()._input_pending_records_domain(record_ids=record_ids)
        domain.append(("type_id.code", "!=", "l10n_pt_spms_verification"))
        return domain

    def exchange_process(self, exchange_record):
        """Hold a processed verification result record whose result is in
        Error, with the reason, so that Retry generates the note again.

        The component stores the result and holds it with its reason
        instead of raising: since 18.0 the process runs in a savepoint,
        and a raise would drop the stored result. edi_oca marks the record
        processed; it is moved to 'Error on process' here with the same
        reason, which is the Retry the design promises.
        """
        res = super().exchange_process(exchange_record)
        if (
            exchange_record.type_id.code == "l10n_pt_spms_verification"
            and exchange_record.edi_exchange_state == "input_processed"
        ):
            result = self.env["spms.invoice.verification"].search(
                [("exchange_record_id", "=", exchange_record.id)], limit=1
            )
            if result.state == "error":
                result._hold_exchange_record(result.error_message)
        return res
