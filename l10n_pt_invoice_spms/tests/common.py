# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from lxml import etree

from odoo.addons.component.tests.common import SavepointComponentCase


class SpmsInvoiceCase(SavepointComponentCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.company.country_id = cls.env.ref("base.pt")
        cls.company.vat = "PT500000000"
        cls.company.spms_username = "test"
        cls.company.spms_password = "test"
        cls.spanish_company = cls.env["res.company"].create(
            {
                "name": "SPMS Spanish test company",
                "country_id": cls.env.ref("base.es").id,
                "currency_id": cls.company.currency_id.id,
            }
        )
        cls.partner = cls.env["res.partner"].create(
            {
                "name": "SPMS shared test customer",
                "spms_information": True,
                "spms_assigned_id": "1234567",
            }
        )
        cls.lot = cls.env["spms.lot"].create({"name": "SPMS test lot", "code": "TST"})
        cls.product = cls.env["product.product"].create(
            {"name": "SPMS test service", "type": "service", "spms_lot_id": cls.lot.id}
        )
        cls.journals = {}
        cls.income_accounts = {}
        for company in cls.company + cls.spanish_company:
            accounts = cls.env["account.account"].with_company(company)
            income = accounts.create(
                {
                    "name": "SPMS test revenue",
                    "code": "SPREV",
                    "company_id": company.id,
                    "user_type_id": cls.env.ref("account.data_account_type_revenue").id,
                }
            )
            receivable = accounts.create(
                {
                    "name": "SPMS test receivable",
                    "code": "SPREC",
                    "company_id": company.id,
                    "reconcile": True,
                    "user_type_id": cls.env.ref(
                        "account.data_account_type_receivable"
                    ).id,
                }
            )
            cls.partner.with_company(
                company
            ).property_account_receivable_id = receivable
            journal = (
                cls.env["account.journal"]
                .with_company(company)
                .create(
                    {
                        "name": "SPMS test sales",
                        "code": "TSPMS",
                        "type": "sale",
                        "company_id": company.id,
                        "default_account_id": income.id,
                        "edi_format_ids": [(5, 0, 0)],
                    }
                )
            )
            cls.journals[company.id] = journal
            cls.income_accounts[company.id] = income
        cls.journal = cls.journals[cls.company.id]
        cls.backend = cls.env.ref("l10n_pt_invoice_spms.spms_backend")
        cls.exchange_type = cls.env.ref("l10n_pt_invoice_spms.spms_exchange_type")

    @classmethod
    def _create_invoice(
        cls,
        company=None,
        invoice_date=None,
        accounting_date=None,
        price_unit=None,
        tax_rate=None,
    ):
        company = company if company is not None else cls.company
        invoice_date = invoice_date if invoice_date is not None else date(2026, 5, 31)
        accounting_date = (
            accounting_date if accounting_date is not None else invoice_date
        )
        price_unit = price_unit if price_unit is not None else 100.0
        tax_rate = tax_rate if tax_rate is not None else 23
        tax = (
            cls.env["account.tax"]
            .with_company(company)
            .create(
                {
                    "name": "SPMS test sales tax",
                    "type_tax_use": "sale",
                    "amount_type": "percent",
                    "amount": tax_rate,
                    "company_id": company.id,
                }
            )
        )
        move = (
            cls.env["account.move"]
            .with_company(company)
            .create(
                {
                    "move_type": "out_invoice",
                    "partner_id": cls.partner.id,
                    "journal_id": cls.journals[company.id].id,
                    "invoice_date": invoice_date,
                    "date": accounting_date,
                    "invoice_line_ids": [
                        (
                            0,
                            0,
                            {
                                "product_id": cls.product.id,
                                "account_id": cls.income_accounts[company.id].id,
                                "quantity": 1,
                                "price_unit": price_unit,
                                "tax_ids": [(6, 0, tax.ids)],
                                "spms_start_date": date(2026, 5, 1),
                                "spms_end_date": date(2026, 5, 31),
                            },
                        )
                    ],
                }
            )
        )
        return move

    @classmethod
    def _create_credit_note(cls, invoice, note_date):
        wizard = cls.env["account.move.reversal"].create(
            {
                "move_ids": [(6, 0, invoice.ids)],
                "refund_method": "refund",
                "date_mode": "custom",
                "date": note_date,
                "reason": "SPMS test reversal",
                "company_id": invoice.company_id.id,
            }
        )
        wizard.reverse_moves()
        return wizard.new_move_ids

    @classmethod
    def _create_debit_note(cls, invoice, note_date):
        wizard = (
            cls.env["account.debit.note"]
            .with_context(active_model="account.move", active_ids=invoice.ids)
            .create(
                {"date": note_date, "reason": "SPMS test debit", "copy_lines": True}
            )
        )
        action = wizard.create_debit()
        return cls.env["account.move"].browse(action["res_id"])

    def _sending_record(self, move, state):
        return self.backend.create_record(
            "l10n_pt_spms",
            {"edi_exchange_state": state, "model": "account.move", "res_id": move.id},
        )

    def _document(self, move):
        """The UBL document the sender builds for a move, before signing."""
        edi_format = self.env.ref("l10n_pt_invoice_spms.spms_edi_format")
        builder = edi_format._get_xml_builder(move.company_id)
        xml_content, errors = builder._export_invoice(move)
        self.assertEqual(errors, set())
        return etree.fromstring(xml_content)

    def _envelope(self, move):
        """The SOAP envelope the sender would post for a move, around a
        stand-in document."""
        component = self.backend._get_component(
            self._sending_record(move, "new"), "send"
        )
        return etree.fromstring(component._prepare_envelope(move, b"<Document/>"))
