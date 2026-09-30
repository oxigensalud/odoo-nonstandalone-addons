# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError

from .common import SpmsInvoiceCase


class TestSpmsCompany(SpmsInvoiceCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other_exchange_type = cls.env["edi.exchange.type"].create(
            {
                "name": "Other test exchange",
                "code": "company_scope_test",
                "backend_id": cls.backend.id,
                "backend_type_id": cls.backend.backend_type_id.id,
                "direction": "output",
            }
        )

    def test_portuguese_invoice_can_create_spms_exchange(self):
        invoice = self._create_invoice()
        invoice.action_post()
        action = invoice.edi_create_exchange_record(self.exchange_type.id)
        exchange = self.env["edi.exchange.record"].browse(action["res_id"])
        self.assertEqual(exchange.record, invoice)
        self.assertEqual(exchange.edi_exchange_state, "new")

    def test_spanish_invoice_cannot_create_spms_exchange(self):
        invoice = self._create_invoice(self.spanish_company)
        invoice.action_post()
        with (
            self.assertRaisesRegex(ValidationError, "Portuguese company"),
            self.cr.savepoint(),
        ):
            invoice.edi_create_exchange_record(self.exchange_type.id)
        self.assertFalse(invoice._has_exchange_record(self.exchange_type))

    def test_direct_create_cannot_bypass_company_check(self):
        invoice = self._create_invoice(self.spanish_company)
        invoice.action_post()
        with (
            self.assertRaisesRegex(ValidationError, "Portuguese company"),
            self.cr.savepoint(),
        ):
            self.env["edi.exchange.record"].create(
                {
                    "backend_id": self.backend.id,
                    "type_id": self.exchange_type.id,
                    "model": "account.move",
                    "res_id": invoice.id,
                }
            )

    def test_exchange_cannot_be_relinked_to_spanish_invoice(self):
        portuguese_invoice = self._create_invoice()
        portuguese_invoice.action_post()
        spanish_invoice = self._create_invoice(self.spanish_company)
        spanish_invoice.action_post()
        action = portuguese_invoice.edi_create_exchange_record(self.exchange_type.id)
        exchange = self.env["edi.exchange.record"].browse(action["res_id"])
        with (
            self.assertRaisesRegex(ValidationError, "Portuguese company"),
            self.cr.savepoint(),
        ):
            exchange.write({"res_id": spanish_invoice.id})
        self.assertEqual(exchange.record, portuguese_invoice)

    def test_portuguese_active_company_does_not_allow_spanish_invoice(self):
        invoice = self._create_invoice(self.spanish_company)
        invoice.action_post()
        invoice = invoice.with_company(self.company)
        with (
            self.assertRaisesRegex(ValidationError, "Portuguese company"),
            self.cr.savepoint(),
        ):
            invoice.edi_create_exchange_record(self.exchange_type.id)

    def test_spms_button_uses_the_invoice_company(self):
        portuguese_invoice = self._create_invoice()
        portuguese_invoice.action_post()
        portuguese_invoice = portuguese_invoice.with_company(self.spanish_company)
        spanish_invoice = self._create_invoice(self.spanish_company)
        spanish_invoice.action_post()
        spanish_invoice = spanish_invoice.with_company(self.company)
        for invoice, available in (
            (portuguese_invoice, True),
            (spanish_invoice, False),
        ):
            with self.subTest(available=available):
                visible_types = {
                    config["type"]["id"] for config in invoice.edi_config.values()
                }
                self.assertEqual(self.exchange_type.id in visible_types, available)

    def test_spanish_active_company_allows_portuguese_invoice(self):
        invoice = self._create_invoice()
        invoice.action_post()
        invoice = invoice.with_company(self.spanish_company)
        action = invoice.edi_create_exchange_record(self.exchange_type.id)
        exchange = self.env["edi.exchange.record"].browse(action["res_id"])
        self.assertEqual(exchange.record, invoice)

    def test_missing_company_country_cannot_create_spms_exchange(self):
        invoice = self._create_invoice()
        invoice.action_post()
        self.company.country_id = False
        with (
            self.assertRaisesRegex(ValidationError, "Portuguese company"),
            self.cr.savepoint(),
        ):
            invoice.edi_create_exchange_record(self.exchange_type.id)

    def test_other_exchange_type_allows_spanish_invoice(self):
        invoice = self._create_invoice(self.spanish_company)
        invoice.action_post()
        exchange_type = self.other_exchange_type
        action = invoice.edi_create_exchange_record(exchange_type.id)
        exchange = self.env["edi.exchange.record"].browse(action["res_id"])
        self.assertEqual(exchange.record, invoice)

    def test_changing_exchange_type_cannot_bypass_company_check(self):
        invoice = self._create_invoice(self.spanish_company)
        invoice.action_post()
        exchange_type = self.other_exchange_type
        action = invoice.edi_create_exchange_record(exchange_type.id)
        exchange = self.env["edi.exchange.record"].browse(action["res_id"])
        with (
            self.assertRaisesRegex(ValidationError, "Portuguese company"),
            self.cr.savepoint(),
        ):
            exchange.write({"type_id": self.exchange_type.id})
        self.assertEqual(exchange.type_id, exchange_type)

    def test_parent_invoice_cannot_bypass_company_check(self):
        invoice = self._create_invoice(self.spanish_company)
        invoice.action_post()
        exchange_type = self.other_exchange_type
        action = invoice.edi_create_exchange_record(exchange_type.id)
        with (
            self.assertRaisesRegex(ValidationError, "Portuguese company"),
            self.cr.savepoint(),
        ):
            self.backend.create_record("l10n_pt_spms", {"parent_id": action["res_id"]})

    def test_parent_relink_cannot_change_spms_child_company(self):
        portuguese_invoice = self._create_invoice()
        portuguese_invoice.action_post()
        spanish_invoice = self._create_invoice(self.spanish_company)
        spanish_invoice.action_post()
        action = portuguese_invoice.edi_create_exchange_record(
            self.other_exchange_type.id
        )
        parent = self.env["edi.exchange.record"].browse(action["res_id"])
        child = self.backend.create_record("l10n_pt_spms", {"parent_id": parent.id})
        self.assertEqual(child.record, portuguese_invoice)
        with (
            self.assertRaisesRegex(ValidationError, "Portuguese company"),
            self.cr.savepoint(),
        ):
            parent.write({"res_id": spanish_invoice.id})
        self.assertEqual(parent.record, portuguese_invoice)
        self.assertEqual(child.record, portuguese_invoice)

    def test_ancestor_relink_cannot_change_spms_descendant_company(self):
        portuguese_invoice = self._create_invoice()
        portuguese_invoice.action_post()
        spanish_invoice = self._create_invoice(self.spanish_company)
        spanish_invoice.action_post()
        action = portuguese_invoice.edi_create_exchange_record(
            self.other_exchange_type.id
        )
        parent = self.env["edi.exchange.record"].browse(action["res_id"])
        middle = self.backend.create_record(
            "company_scope_test", {"parent_id": parent.id}
        )
        child = self.backend.create_record("l10n_pt_spms", {"parent_id": middle.id})
        self.assertEqual(child.record, portuguese_invoice)
        with (
            self.assertRaisesRegex(ValidationError, "Portuguese company"),
            self.cr.savepoint(),
        ):
            parent.write({"res_id": spanish_invoice.id})
        self.assertEqual(child.record, portuguese_invoice)

    def test_batch_parent_relink_preserves_all_documents_on_failure(self):
        first_invoice = self._create_invoice()
        first_invoice.action_post()
        second_invoice = self._create_invoice()
        second_invoice.action_post()
        spanish_invoice = self._create_invoice(self.spanish_company)
        spanish_invoice.action_post()
        first_action = first_invoice.edi_create_exchange_record(
            self.other_exchange_type.id
        )
        second_action = second_invoice.edi_create_exchange_record(
            self.other_exchange_type.id
        )
        first = self.env["edi.exchange.record"].browse(first_action["res_id"])
        second = self.env["edi.exchange.record"].browse(second_action["res_id"])
        child = self.backend.create_record("l10n_pt_spms", {"parent_id": second.id})
        parents = first + second
        with (
            self.assertRaisesRegex(ValidationError, "Portuguese company"),
            self.cr.savepoint(),
        ):
            parents.write({"res_id": spanish_invoice.id})
        self.assertEqual(first.record, first_invoice)
        self.assertEqual(second.record, second_invoice)
        self.assertEqual(child.record, second_invoice)

    def test_parent_relink_preserves_explicit_child_document(self):
        portuguese_invoice = self._create_invoice()
        portuguese_invoice.action_post()
        spanish_invoice = self._create_invoice(self.spanish_company)
        spanish_invoice.action_post()
        action = portuguese_invoice.edi_create_exchange_record(
            self.other_exchange_type.id
        )
        parent = self.env["edi.exchange.record"].browse(action["res_id"])
        child = self.backend.create_record(
            "l10n_pt_spms",
            {
                "parent_id": parent.id,
                "model": "account.move",
                "res_id": portuguese_invoice.id,
            },
        )
        parent.write({"res_id": spanish_invoice.id})
        self.assertEqual(parent.record, spanish_invoice)
        self.assertEqual(child.record, portuguese_invoice)

    def test_parent_relink_checks_hidden_spms_children(self):
        portuguese_invoice = self._create_invoice()
        portuguese_invoice.action_post()
        spanish_invoice = self._create_invoice(self.spanish_company)
        spanish_invoice.action_post()
        action = portuguese_invoice.edi_create_exchange_record(
            self.other_exchange_type.id
        )
        parent = self.env["edi.exchange.record"].browse(action["res_id"])
        child = self.backend.create_record("l10n_pt_spms", {"parent_id": parent.id})
        user = (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "SPMS company rule test user",
                    "login": "spms_company_rule@example.invalid",
                    "company_id": self.company.id,
                    "company_ids": [(6, 0, [self.company.id, self.spanish_company.id])],
                    "groups_id": [
                        (
                            6,
                            0,
                            [
                                self.env.ref("base.group_user").id,
                                self.env.ref("base_edi.group_edi_user").id,
                                self.env.ref("account.group_account_invoice").id,
                            ],
                        )
                    ],
                }
            )
        )
        self.env["ir.rule"].create(
            {
                "name": "Hide SPMS exchanges in company rule test",
                "model_id": self.env.ref("edi_core_oca.model_edi_exchange_record").id,
                "domain_force": "[('type_id.code', '!=', 'l10n_pt_spms')]",
            }
        )
        visible = self.env["edi.exchange.record"].with_user(user)
        self.assertEqual(visible.search([("id", "=", parent.id)]).ids, [parent.id])
        self.assertFalse(visible.search([("id", "=", child.id)]))
        with (
            self.assertRaisesRegex(ValidationError, "Portuguese company"),
            self.cr.savepoint(),
        ):
            parent.with_user(user).write({"res_id": spanish_invoice.id})
        self.assertEqual(child.record, portuguese_invoice)
