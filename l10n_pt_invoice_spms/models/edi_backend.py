# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from zeep.exceptions import Fault, TransportError

from odoo import models


class EdiBackend(models.Model):
    _inherit = "edi.backend"

    def _send_retryable_exceptions(self):
        """An HTTP error answer of the CCF (a SOAP fault, an empty or
        unreadable 500) is retried by the queue like a connection problem,
        as the requests.HTTPError of the hand-built envelope used to be.
        zeep raises them as Fault or TransportError, which are not IOErrors.
        """
        retryable = super()._send_retryable_exceptions()
        if self.backend_type_id.code == "l10n_pt_spms":
            retryable += (Fault, TransportError)
        return retryable
