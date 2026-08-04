# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.tools import html_escape


class AccountMove(models.Model):
    _inherit = "account.move"

    spms_return_invoice_ids = fields.One2many(
        comodel_name="spms.return.invoice",
        inverse_name="move_id",
        string="SPMS Return Invoices",
    )
    spms_credit_note_return_invoice_ids = fields.One2many(
        comodel_name="spms.return.invoice",
        inverse_name="credit_note_move_id",
        string="SPMS Return Invoices (Credit Note)",
    )
    spms_return_invoice_count = fields.Integer(
        string="# SPMS Returns",
        compute="_compute_spms_return_invoice_count",
    )

    @api.depends("spms_return_invoice_ids")
    def _compute_spms_return_invoice_count(self):
        for rec in self:
            rec.spms_return_invoice_count = len(rec.spms_return_invoice_ids)

    def action_view_spms_return_invoices(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("SPMS Returns"),
            "res_model": "spms.return.invoice",
            "view_mode": "tree,form",
            "domain": [("move_id", "=", self.id)],
        }

    def button_cancel(self):
        """Release the linked SPMS invoices in the same transaction.

        The pointer means 'live credit note': once the note is cancelled
        the invoice must become generable again on its own — no re-link,
        no manual step — leaving a trace in the return's chatter.
        """
        res = super().button_cancel()
        invoices = self.spms_credit_note_return_invoice_ids
        for invoice in invoices:
            invoice.return_id.message_post(
                body=_(
                    "Credit note %(note)s was cancelled: invoice %(invoice)s "
                    "is pending generation again."
                )
                % {
                    "note": html_escape(invoice.credit_note_move_id.display_name),
                    "invoice": html_escape(invoice.name),
                },
                subtype_xmlid="mail.mt_note",
            )
        invoices.write({"credit_note_move_id": False})
        invoices._update_state()
        return res

    def unlink(self):
        invoices = self.spms_credit_note_return_invoice_ids
        res = super().unlink()
        # the database already dropped the pointers (ondelete='set null');
        # the semaphore has to follow without waiting for a re-link
        invoices._update_state()
        return res
