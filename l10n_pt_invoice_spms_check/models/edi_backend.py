# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models


class EdiBackend(models.Model):
    _inherit = "edi.backend"

    def _input_pending_records_domain(self, record_ids=None):
        """Leave the waiting check results to the module's own polling.

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
        domain.append(("type_id.code", "!=", "l10n_pt_spms_check"))
        return domain
