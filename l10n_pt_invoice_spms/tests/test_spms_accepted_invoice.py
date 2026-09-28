# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo.exceptions import UserError

from .common import SpmsInvoiceCase


class TestSpmsAcceptedInvoice(SpmsInvoiceCase):
    """An invoice SPMS has accepted stays posted."""

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
