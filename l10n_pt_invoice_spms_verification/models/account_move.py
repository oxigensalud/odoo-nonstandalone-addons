# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.tools import html_escape


class AccountMove(models.Model):
    _inherit = "account.move"

    spms_invoice_verification_ids = fields.One2many(
        comodel_name="spms.invoice.verification",
        inverse_name="move_id",
        string="SPMS Invoice Verifications",
    )
    spms_note_invoice_verification_ids = fields.One2many(
        comodel_name="spms.invoice.verification",
        inverse_name="note_move_id",
        string="SPMS Invoice Verifications (Credit/Debit Note)",
    )
    spms_invoice_verification_state = fields.Selection(
        related="spms_invoice_verification_ids.verification_state",
        string="SPMS Verification State",
    )

    def action_view_spms_invoice_verification(self):
        """Open the invoice's verification result form directly (1:1)."""
        self.ensure_one()
        result = self.spms_invoice_verification_ids[:1]
        if not result:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Invoice Verification"),
            "res_model": "spms.invoice.verification",
            "res_id": result.id,
            "view_mode": "form",
        }

    def button_cancel(self):
        """Release the linked verification results in the same transaction.

        The pointer means 'live note': once the note is cancelled
        the result must become generable again on its own — no manual
        step — leaving a trace in the original invoice's chatter.
        """
        res = super().button_cancel()
        results = self.spms_note_invoice_verification_ids
        for result in results:
            result.move_id.message_post(
                body=_(
                    "The generated note %(note)s was cancelled: the verification "
                    "result of invoice %(invoice)s is ready for generation "
                    "again."
                )
                % {
                    "note": html_escape(result.note_move_id.display_name),
                    "invoice": html_escape(result.move_id.display_name),
                },
                subtype_xmlid="mail.mt_note",
            )
        results.write({"note_move_id": False})
        return res
