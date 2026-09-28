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
        action = invoice.edi_create_exchange_record(self.exchange_type.id)
        exchange = self.env["edi.exchange.record"].browse(action["res_id"])
        self.assertEqual(exchange.record, invoice)
        self.assertEqual(exchange.edi_exchange_state, "new")

    def test_spanish_invoice_cannot_create_spms_exchange(self):
        invoice = self._create_invoice(self.spanish_company)
        with self.assertRaisesRegex(
            ValidationError, "Portuguese company"
        ), self.cr.savepoint():
            invoice.edi_create_exchange_record(self.exchange_type.id)
        self.assertFalse(invoice._has_exchange_record(self.exchange_type))

    def test_direct_create_cannot_bypass_company_check(self):
        invoice = self._create_invoice(self.spanish_company)
        with self.assertRaisesRegex(
            ValidationError, "Portuguese company"
        ), self.cr.savepoint():
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
        spanish_invoice = self._create_invoice(self.spanish_company)
        action = portuguese_invoice.edi_create_exchange_record(self.exchange_type.id)
        exchange = self.env["edi.exchange.record"].browse(action["res_id"])
        with self.assertRaisesRegex(
            ValidationError, "Portuguese company"
        ), self.cr.savepoint():
            exchange.write({"res_id": spanish_invoice.id})
        self.assertEqual(exchange.record, portuguese_invoice)

    def test_portuguese_active_company_does_not_allow_spanish_invoice(self):
        invoice = self._create_invoice(self.spanish_company).with_company(self.company)
        with self.assertRaisesRegex(
            ValidationError, "Portuguese company"
        ), self.cr.savepoint():
            invoice.edi_create_exchange_record(self.exchange_type.id)

    def test_spms_button_uses_the_invoice_company(self):
        portuguese_invoice = self._create_invoice().with_company(self.spanish_company)
        spanish_invoice = self._create_invoice(self.spanish_company).with_company(
            self.company
        )
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
        invoice = self._create_invoice().with_company(self.spanish_company)
        action = invoice.edi_create_exchange_record(self.exchange_type.id)
        exchange = self.env["edi.exchange.record"].browse(action["res_id"])
        self.assertEqual(exchange.record, invoice)

    def test_missing_company_country_cannot_create_spms_exchange(self):
        invoice = self._create_invoice()
        self.company.country_id = False
        with self.assertRaisesRegex(
            ValidationError, "Portuguese company"
        ), self.cr.savepoint():
            invoice.edi_create_exchange_record(self.exchange_type.id)

    def test_other_exchange_type_allows_spanish_invoice(self):
        invoice = self._create_invoice(self.spanish_company)
        exchange_type = self.other_exchange_type
        action = invoice.edi_create_exchange_record(exchange_type.id)
        exchange = self.env["edi.exchange.record"].browse(action["res_id"])
        self.assertEqual(exchange.record, invoice)

    def test_changing_exchange_type_cannot_bypass_company_check(self):
        invoice = self._create_invoice(self.spanish_company)
        exchange_type = self.other_exchange_type
        action = invoice.edi_create_exchange_record(exchange_type.id)
        exchange = self.env["edi.exchange.record"].browse(action["res_id"])
        with self.assertRaisesRegex(
            ValidationError, "Portuguese company"
        ), self.cr.savepoint():
            exchange.write({"type_id": self.exchange_type.id})
        self.assertEqual(exchange.type_id, exchange_type)

    def test_parent_invoice_cannot_bypass_company_check(self):
        invoice = self._create_invoice(self.spanish_company)
        exchange_type = self.other_exchange_type
        action = invoice.edi_create_exchange_record(exchange_type.id)
        with self.assertRaisesRegex(
            ValidationError, "Portuguese company"
        ), self.cr.savepoint():
            self.backend.create_record("l10n_pt_spms", {"parent_id": action["res_id"]})
