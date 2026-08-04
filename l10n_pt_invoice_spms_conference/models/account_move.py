# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.tools import html_escape


class AccountMove(models.Model):
    _inherit = "account.move"

    spms_conference_result_ids = fields.One2many(
        comodel_name="spms.conference.result",
        inverse_name="move_id",
        string="SPMS Conference Results",
    )
    spms_conference_credit_note_result_ids = fields.One2many(
        comodel_name="spms.conference.result",
        inverse_name="credit_note_move_id",
        string="SPMS Conference Results (Credit Note)",
    )
    spms_conference_result_count = fields.Integer(
        string="# SPMS Conference Results",
        compute="_compute_spms_conference_result_count",
    )
    spms_conference_error_count = fields.Integer(
        string="# SPMS Conference Errors",
        compute="_compute_spms_conference_result_count",
    )

    @api.depends(
        "spms_conference_result_ids",
        "spms_conference_result_ids.error_ids",
    )
    def _compute_spms_conference_result_count(self):
        for rec in self:
            rec.spms_conference_result_count = len(rec.spms_conference_result_ids)
            rec.spms_conference_error_count = len(
                rec.spms_conference_result_ids.error_ids
            )

    def action_view_spms_conference_result(self):
        """Open the invoice's conference result form directly (1:1)."""
        self.ensure_one()
        result = self.spms_conference_result_ids[:1]
        if not result:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Conference Result"),
            "res_model": "spms.conference.result",
            "res_id": result.id,
            "view_mode": "form",
        }

    def button_cancel(self):
        """Release the linked conference results in the same transaction.

        The pointer means 'live credit note': once the note is cancelled
        the result must become generable again on its own — no manual
        step — leaving a trace in the original invoice's chatter.
        """
        res = super().button_cancel()
        results = self.spms_conference_credit_note_result_ids
        for result in results:
            result.move_id.message_post(
                body=_(
                    "Credit note %(note)s was cancelled: the conference "
                    "result of invoice %(invoice)s is ready for generation "
                    "again."
                )
                % {
                    "note": html_escape(result.credit_note_move_id.display_name),
                    "invoice": html_escape(result.move_id.display_name),
                },
                subtype_xmlid="mail.mt_note",
            )
        results.write({"credit_note_move_id": False})
        results._update_state()
        return res

    def unlink(self):
        results = self.spms_conference_credit_note_result_ids
        res = super().unlink()
        # the database already dropped the pointers (ondelete='set null');
        # the semaphore has to follow without waiting for a manual refresh
        results._update_state()
        return res
