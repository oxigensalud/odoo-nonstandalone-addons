# Copyright 2025 Dixmit
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from datetime import timedelta

from odoo import models


class AccountEdiFormat(models.Model):

    _inherit = "account.edi.format"

    def _get_xml_builder(self, company):
        """Override to return the SPMS XML builder."""
        if self.code == "spms_cius_pt_211" and company.country_id.code == "PT":
            return self.env["account.edi.xml.spms_cius_pt_211"]
        return super()._get_xml_builder(company)


class AccountEdiXmlSpmsCiusPt211(models.AbstractModel):
    _name = "account.edi.xml.spms_cius_pt_211"
    _inherit = "account.edi.xml.ubl_20"
    _description = "SPMS CIUS PT 2.11 XML Builder"

    def _get_invoice_period_vals_list(self, invoice):
        start_date = min(invoice.invoice_line_ids.mapped("spms_start_date"))
        if start_date:
            start_date = start_date.isoformat()
        end_date = max(invoice.invoice_line_ids.mapped("spms_end_date"))
        end_date = end_date.replace(day=28)
        end_date += timedelta(days=4)
        end_date = end_date - timedelta(days=end_date.day)
        if end_date:
            end_date = end_date.isoformat()
        return [{"start_date": start_date, "end_date": end_date}]

    def _get_partner_party_vals(self, partner, role):
        vals = super()._get_partner_party_vals(partner, role)
        if role == "customer":
            vals.update(
                {
                    "party_legal_entity_vals": [],
                }
            )
        return vals

    def _export_invoice_vals(self, invoice):
        # EXTENDS account.edi.xml.ubl_20
        vals = super()._export_invoice_vals(invoice)

        vals.update(
            {
                "InvoiceType_template": "l10n_pt_invoice_spms.spms_cius_pt_211_InvoiceType",
                "InvoiceExtension_spms": "l10n_pt_invoice_spms.spms_cius_pt_211_InvoiceExtension_spms",  # noqa: disable=B950
                "InvoiceLineType_template": "l10n_pt_invoice_spms.spms_cius_pt_211_InvoiceLine",
                "PartyType_template": "l10n_pt_invoice_spms.spms_cius_pt_211_PartyType",
                "AddressType_template": "l10n_pt_invoice_spms.spms_cius_pt_211_AddressType",
                "TaxCategoryType_template": "l10n_pt_invoice_spms.spms_cius_pt_211_TaxCategoryType",  # noqa: disable=B950
            }
        )
        vals["vals"].update(
            {
                "id": invoice._get_spms_invoice_number(),
            }
        )

        lots = invoice.invoice_line_ids.product_id.spms_lot_id
        lotes = []
        lot_number = 1
        for lot in lots:
            lines = invoice.invoice_line_ids.filtered(
                lambda l: l.product_id.spms_lot_id == lot
            )
            lotes.append(
                {
                    "numero": lot_number,
                    "tipo": lot.code,
                    "amount": sum(lines.mapped("price_subtotal")),
                    "total": len(lines),
                    "dispensas": [
                        {
                            "prescription": line.spms_prescription,
                            "user_number": line.spms_user_number,
                            "beneficiary_number": line.spms_beneficiary_number,
                            "prescription_type": line.spms_prescription_type_id.code,
                            "price_subtotal": line.price_subtotal,
                            "price_unit": line.price_unit,
                            "quantity": int(line.quantity),
                            "context": line.spms_context_id.code,
                            "line_number": line.id,
                            "system": line.product_id.default_code,
                            "suspension_reason": line.spms_suspension_reason_id.code,
                            "start_date": line.spms_start_date
                            and line.spms_start_date.isoformat(),
                            "end_date": line.spms_end_date
                            and line.spms_end_date.isoformat(),
                        }
                        for line in lines
                    ],
                }
            )
            lot_number += 1
        vals["vals"].update(
            {
                "spms_crd_vals": {
                    "valor_total_prestacoes": invoice.amount_total,
                    "currency_dp": invoice.currency_id.decimal_places,
                    "quantidade_total_prestacoes": len(invoice.invoice_line_ids),
                    "numero_lotes": len(lots),
                    "lotes": lotes,
                },
                "profile_id": False,
                "ubl_version_id": "UBL 2.0 CS (2006.10) + SIC (2007.03)",
                "customization_id": "1.0",
                "due_date": False,
                "invoice_type_code": "FF",
                "customer_assigned_account_id": invoice.partner_id.spms_assigned_id,
            }
        )

        return vals

    def _get_invoice_line_price_vals(self, line):
        result = super()._get_invoice_line_price_vals(line)
        result.update(
            {
                "spms_tipo": line.product_id.spms_lot_id.code
                if line.product_id.spms_lot_id
                else "",
            }
        )
        return result

    def _get_partner_party_tax_scheme_vals_list(self, partner, role):
        result = super()._get_partner_party_tax_scheme_vals_list(partner, role)
        for line in result:
            line["tax_scheme_id"] = "PT IVA"
        return result
