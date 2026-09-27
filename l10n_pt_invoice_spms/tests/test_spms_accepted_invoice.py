# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from datetime import date

from odoo.exceptions import UserError
from odoo.tests.common import SavepointCase


class TestSpmsAcceptedInvoice(SavepointCase):
    """An invoice SPMS has accepted stays posted."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        income_account = cls.env["account.account"].search(
            [
                ("company_id", "=", cls.company.id),
                (
                    "user_type_id",
                    "=",
                    cls.env.ref("account.data_account_type_revenue").id,
                ),
            ],
            limit=1,
        )
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "SPMS Test Sales",
                "code": "TSPMS",
                "type": "sale",
                "company_id": cls.company.id,
                "default_account_id": income_account.id,
                "edi_format_ids": [(5, 0, 0)],
            }
        )
        cls.partner = cls.env["res.partner"].create({"name": "SPMS Test Partner"})
        cls.product = cls.env["product.product"].create(
            {"name": "Oxygen therapy test", "type": "service"}
        )
        cls.backend = cls.env.ref("l10n_pt_invoice_spms.spms_backend")

    @classmethod
    def _create_invoice(cls):
        move = cls.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": cls.partner.id,
                "journal_id": cls.journal.id,
                "invoice_date": date(2026, 5, 31),
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": cls.product.id,
                            "quantity": 1,
                            "price_unit": 100.0,
                        },
                    )
                ],
            }
        )
        move.action_post()
        return move

    def _sending_record(self, move, state):
        return self.backend.create_record(
            "l10n_pt_spms",
            {"edi_exchange_state": state, "model": "account.move", "res_id": move.id},
        )

    def test_accepted_invoice_cannot_be_cancelled(self):
        invoice = self._create_invoice()
        self._sending_record(invoice, "output_sent_and_processed")
        with self.assertRaisesRegex(UserError, "can no longer be cancelled"):
            invoice.button_cancel()
        self.assertEqual(invoice.state, "posted")

    def test_accepted_invoice_cannot_be_reset_to_draft(self):
        invoice = self._create_invoice()
        self._sending_record(invoice, "output_sent_and_processed")
        with self.assertRaisesRegex(UserError, "reset to draft"):
            invoice.button_draft()
        self.assertEqual(invoice.state, "posted")

    def test_sent_invoice_is_kept_posted_on_a_direct_write(self):
        invoice = self._create_invoice()
        self._sending_record(invoice, "output_sent")
        with self.assertRaises(UserError):
            invoice.write({"state": "cancel"})
        self.assertEqual(invoice.state, "posted")

    def test_unsent_invoice_can_be_cancelled(self):
        for state in (None, "output_pending", "output_error_on_send"):
            with self.subTest(state=state):
                invoice = self._create_invoice()
                if state is not None:
                    self._sending_record(invoice, state)
                invoice.button_cancel()
                self.assertEqual(invoice.state, "cancel")

    def test_cancelled_invoice_can_be_posted_again(self):
        """An invoice cancelled before the rule existed goes back to what
        SPMS holds through draft and post."""
        invoice = self._create_invoice()
        invoice.button_cancel()
        self._sending_record(invoice, "output_sent_and_processed")
        invoice.button_draft()
        self.assertEqual(invoice.state, "draft")
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")
