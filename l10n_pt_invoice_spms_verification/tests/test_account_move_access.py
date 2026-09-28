# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import Form, tagged
from odoo.tests.common import SavepointCase


@tagged("post_install", "-at_install")
class TestAccountMoveAccess(SavepointCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.partner = cls.env["res.partner"].create(
            {"name": "Accounting access test partner"}
        )
        for kind in ("receivable", "payable"):
            account = cls.env["account.account"].create(
                {
                    "name": "Access test " + kind,
                    "code": "ACCESS" + kind.upper(),
                    "company_id": cls.company.id,
                    "reconcile": True,
                    "user_type_id": cls.env.ref("account.data_account_type_" + kind).id,
                }
            )
            cls.partner["property_account_" + kind + "_id"] = account
        cls.journals = {}
        for kind, code in (
            ("sale", "ATSL"),
            ("purchase", "ATPU"),
            ("general", "ATGN"),
            ("bank", "ATBK"),
        ):
            cls.journals[kind] = cls.env["account.journal"].create(
                {
                    "name": "Accounting access test " + kind,
                    "code": code,
                    "type": kind,
                    "company_id": cls.company.id,
                    "edi_format_ids": [(5, 0, 0)],
                }
            )
        cls.user = (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Accounting access test user",
                    "login": "accounting_access_test@example.invalid",
                    "company_id": cls.company.id,
                    "company_ids": [(6, 0, cls.company.ids)],
                    "groups_id": [
                        (
                            6,
                            0,
                            [
                                cls.env.ref("base.group_user").id,
                                cls.env.ref("account.group_account_invoice").id,
                            ],
                        )
                    ],
                }
            )
        )
        cls.consultation = cls.env.ref(
            "l10n_pt_invoice_spms_verification.spms_invoice_verification_group_consultation"
        )

    def _draft(self, move_type):
        journal_type = {
            "out_invoice": "sale",
            "in_invoice": "purchase",
            "entry": "general",
        }[move_type]
        invoice = self.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": self.partner.id,
                "journal_id": self.journals[journal_type].id,
            }
        )
        invoice.flush()
        self.env.cache.invalidate()
        return invoice.with_user(self.user)

    def test_open_customer_draft_without_spms_access(self):
        self.assertNotIn(self.consultation, self.user.groups_id)
        invoice = self._draft("out_invoice")
        form = Form(invoice)
        self.assertEqual(form.partner_id, self.partner)
        self.assertNotIn("spms_invoice_verification_ids", form._view["fields"])

    def test_open_vendor_draft_without_spms_access(self):
        invoice = self._draft("in_invoice")
        form = Form(invoice)
        self.assertEqual(form.partner_id, self.partner)
        self.assertNotIn("spms_invoice_verification_ids", form._view["fields"])

    def test_spms_consultation_keeps_the_verification_field(self):
        self.user.groups_id = [(4, self.consultation.id)]
        form = Form(self._draft("out_invoice"))
        self.assertIn("spms_invoice_verification_ids", form._view["fields"])

    def test_cancel_customer_draft_without_spms_access(self):
        invoice = self._draft("out_invoice")
        invoice.button_cancel()
        self.assertEqual(invoice.state, "cancel")

    def test_cancel_vendor_draft_without_spms_access(self):
        invoice = self._draft("in_invoice")
        invoice.button_cancel()
        self.assertEqual(invoice.state, "cancel")

    def test_delete_customer_draft_without_spms_access(self):
        invoice = self._draft("out_invoice")
        invoice.unlink()
        self.assertFalse(invoice.exists())

    def test_delete_vendor_draft_without_spms_access(self):
        invoice = self._draft("in_invoice")
        invoice.unlink()
        self.assertFalse(invoice.exists())

    def test_accounting_user_does_not_gain_spms_read_access(self):
        with self.assertRaises(AccessError):
            self.env["spms.invoice.verification"].with_user(self.user).search([])

    def test_read_customer_draft_without_spms_access(self):
        invoice = self._draft("out_invoice")
        fields = invoice.fields_get(
            [
                "id",
                "spms_invoice_verification_ids",
                "spms_note_invoice_verification_ids",
            ]
        )
        values = invoice.read(list(fields))[0]
        self.assertEqual(values["id"], invoice.id)
        self.assertNotIn("spms_invoice_verification_ids", values)
        self.assertNotIn("spms_note_invoice_verification_ids", values)

    def test_optional_fields_follow_spms_access_on_delegated_models(self):
        for model in ("account.move", "account.payment", "account.bank.statement.line"):
            with self.subTest(model=model):
                fields = self.env[model].with_user(self.user).fields_get()
                self.assertNotIn("spms_invoice_verification_ids", fields)
                self.assertNotIn("spms_note_invoice_verification_ids", fields)

        self.user.groups_id = [(4, self.consultation.id)]
        for model in ("account.move", "account.payment", "account.bank.statement.line"):
            with self.subTest(model=model):
                fields = self.env[model].with_user(self.user).fields_get()
                self.assertIn("spms_invoice_verification_ids", fields)
                self.assertIn("spms_note_invoice_verification_ids", fields)

    def test_payment_lifecycle_without_spms_access(self):
        for creator in (self.env.user, self.user):
            with self.subTest(created_by_accounting_user=creator == self.user):
                payment = (
                    self.env["account.payment"]
                    .with_user(creator)
                    .create(
                        {
                            "payment_type": "outbound",
                            "partner_type": "supplier",
                            "partner_id": self.partner.id,
                            "amount": 10.0,
                            "journal_id": self.journals["bank"].id,
                            "payment_method_id": self.env.ref(
                                "account.account_payment_method_manual_out"
                            ).id,
                        }
                    )
                )
                payment.flush()
                self.env.cache.invalidate()
                payment = payment.with_user(self.user)
                fields = payment.fields_get(
                    [
                        "id",
                        "spms_invoice_verification_ids",
                        "spms_note_invoice_verification_ids",
                    ]
                )
                values = payment.read(list(fields))[0]
                self.assertEqual(values["id"], payment.id)
                self.assertNotIn("spms_invoice_verification_ids", values)
                self.assertNotIn("spms_note_invoice_verification_ids", values)
                form = Form(payment)
                self.assertEqual(form.amount, 10.0)
                payment.action_post()
                self.assertEqual(payment.state, "posted")
                payment.action_draft()
                payment.action_cancel()
                self.assertEqual(payment.state, "cancel")
                move = payment.move_id
                payment.unlink()
                self.assertFalse(payment.exists())
                self.assertFalse(move.exists())

    def test_read_bank_statement_line_without_spms_access(self):
        self.user.groups_id = [(4, self.env.ref("account.group_account_user").id)]
        statement = self.env["account.bank.statement"].create(
            {"journal_id": self.journals["bank"].id}
        )
        line = self.env["account.bank.statement.line"].create(
            {
                "statement_id": statement.id,
                "payment_ref": "Synthetic bank transaction",
                "amount": 10.0,
                "partner_id": self.partner.id,
            }
        )
        line.flush()
        self.env.cache.invalidate()
        line = line.with_user(self.user)
        fields = line.fields_get(
            [
                "id",
                "spms_invoice_verification_ids",
                "spms_note_invoice_verification_ids",
            ]
        )
        values = line.read(list(fields))[0]
        self.assertEqual(values["id"], line.id)
        self.assertNotIn("spms_invoice_verification_ids", values)
        self.assertNotIn("spms_note_invoice_verification_ids", values)

    def test_open_journal_entry_without_spms_access(self):
        entry = self._draft("entry")
        form = Form(entry)
        self.assertEqual(form.move_type, "entry")
        self.assertNotIn("spms_invoice_verification_ids", form._view["fields"])

    def test_cancel_journal_entry_without_spms_access(self):
        entry = self._draft("entry")
        entry.button_cancel()
        self.assertEqual(entry.state, "cancel")

    def test_delete_journal_entry_without_spms_access(self):
        entry = self._draft("entry")
        entry.unlink()
        self.assertFalse(entry.exists())

    def test_open_posted_journal_entry_without_spms_access(self):
        entry = self._draft("entry")
        entry.write(
            {
                "date": "2026-01-10",
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Synthetic debit",
                            "account_id": self.partner.property_account_receivable_id.id,
                            "partner_id": self.partner.id,
                            "debit": 10.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "name": "Synthetic credit",
                            "account_id": self.partner.property_account_payable_id.id,
                            "partner_id": self.partner.id,
                            "credit": 10.0,
                        },
                    ),
                ],
            }
        )
        entry.action_post()
        entry.flush()
        self.env.cache.invalidate()
        form = Form(entry)
        self.assertEqual(form.state, "posted")
        self.assertNotIn("spms_invoice_verification_ids", form._view["fields"])
