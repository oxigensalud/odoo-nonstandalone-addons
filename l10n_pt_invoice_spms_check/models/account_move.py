# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.tools import html_escape


class AccountMove(models.Model):
    _inherit = "account.move"

    spms_invoice_check_ids = fields.One2many(
        comodel_name="spms.invoice.check",
        inverse_name="move_id",
        string="SPMS Invoice Checks",
    )
    spms_note_invoice_check_ids = fields.One2many(
        comodel_name="spms.invoice.check",
        inverse_name="note_move_id",
        string="SPMS Invoice Checks (Credit/Debit Note)",
    )
    spms_invoice_check_state = fields.Selection(
        related="spms_invoice_check_ids.check_state",
        string="SPMS Check State",
    )

    def action_view_spms_invoice_check(self):
        """Open the invoice's check result form directly (1:1)."""
        self.ensure_one()
        result = self.spms_invoice_check_ids[:1]
        if not result:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Invoice Check"),
            "res_model": "spms.invoice.check",
            "res_id": result.id,
            "view_mode": "form",
        }

    def button_cancel(self):
        """Release the linked check results in the same transaction.

        The pointer means 'live note': once the note is cancelled
        the result must become generable again on its own — no manual
        step — leaving a trace in the original invoice's chatter.
        """
        res = super().button_cancel()
        results = self.spms_note_invoice_check_ids
        for result in results:
            result.move_id.message_post(
                body=_(
                    "The generated note %(note)s was cancelled: the check "
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
        results._update_state()
        return res

    def unlink(self):
        results = self.spms_note_invoice_check_ids
        res = super().unlink()
        # the database already dropped the pointers (ondelete='set null');
        # the semaphore has to follow without waiting for a manual refresh
        results._update_state()
        return res
