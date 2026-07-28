# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import io
import logging
from datetime import timedelta

import openpyxl

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare, float_is_zero, float_round

from .spms_return_invoice_line import SPMS_TAX_FACTOR

_logger = logging.getLogger(__name__)

# Error-report columns required by the import, by Excel header. The optional
# ones (QUANTIDADETOTAL, "Numero de dias pagos", SISTEMAPRESTADOCRD) are read
# when present. Sensitive columns (patient name/ids, prescriber, convention
# text) are deliberately NOT read.
ERROR_FILE_HEADERS = (
    "NUMFACTURA",
    "NUMEROPRESCRICAO",
    "VALORTOTAL",
    "VALORTOTALAPURADO",
    "VALORTOTALAPURADOIVA",
    "COD_ERRO",
    "DESC_ERRO",
)


def _cell_value(row, col_index, header):
    index = col_index.get(header)
    if index is None or index >= len(row):
        return None
    return row[index]


def _cell_text(value):
    if value is None:
        return False
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip() or False


def _cell_amount(value):
    return float(value or 0.0)


class SpmsReturn(models.Model):
    _name = "spms.return"
    _description = "SPMS Return"
    _order = "period desc, id desc"

    name = fields.Char(
        string="Name",
        compute="_compute_name",
        store=True,
    )
    period = fields.Char(
        string="Period",
        required=True,
        default=lambda self: self._default_period(),
        help="SPMS conference period, in YYYYMM format (e.g. 202605).",
    )
    date = fields.Date(
        string="Date",
        required=True,
        default=fields.Date.context_today,
        help="Date the error report was received.",
    )
    file = fields.Binary(
        string="Error File",
        attachment=True,
        help="SNS/CCMSNS conference error report (Excel), as received.",
    )
    file_name = fields.Char(
        string="Error File Name",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("processed", "Processed"),
            ("done", "Done"),
            ("cancel", "Cancelled"),
        ],
        string="State",
        required=True,
        default="draft",
        copy=False,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    invoice_ids = fields.One2many(
        comodel_name="spms.return.invoice",
        inverse_name="return_id",
        string="Invoices",
        copy=False,
    )
    invoice_count = fields.Integer(
        string="# Invoices",
        compute="_compute_invoice_count",
    )
    line_count = fields.Integer(
        string="# Prescriptions",
        compute="_compute_counts",
    )
    error_count = fields.Integer(
        string="# Errors",
        compute="_compute_counts",
    )

    @api.model
    def _default_period(self):
        today = fields.Date.context_today(self)
        last_day_previous_month = today.replace(day=1) - timedelta(days=1)
        return last_day_previous_month.strftime("%Y%m")

    @api.depends("period")
    def _compute_name(self):
        for rec in self:
            rec.name = rec.period and _("SPMS %s") % rec.period or False

    @api.depends("invoice_ids")
    def _compute_invoice_count(self):
        for rec in self:
            rec.invoice_count = len(rec.invoice_ids)

    @api.depends("invoice_ids.line_ids", "invoice_ids.line_ids.error_ids")
    def _compute_counts(self):
        for rec in self:
            lines = rec.invoice_ids.mapped("line_ids")
            rec.line_count = len(lines)
            rec.error_count = len(lines.mapped("error_ids"))

    @api.constrains("period")
    def _check_period(self):
        for rec in self:
            period = rec.period or ""
            if not (
                len(period) == 6 and period.isdigit() and 1 <= int(period[4:]) <= 12
            ):
                raise ValidationError(
                    _("Invalid SPMS period '%s': expected YYYYMM format.") % rec.period
                )

    def action_process(self):
        for rec in self:
            rec._check_can_process()
            rows = rec._parse_error_file()
            snapshot = rec._snapshot_children()
            rec.invoice_ids.unlink()
            rec._create_children(rows, snapshot)
            rec.invoice_ids._update_state()
            rec.state = "processed"
        return True

    def action_back_to_draft(self):
        self._check_responsible()
        for rec in self:
            if rec.state not in ("processed", "cancel"):
                raise UserError(
                    _(
                        "Only processed or cancelled SPMS returns can be set "
                        "back to draft."
                    )
                )
            rec.state = "draft"
        return True

    def action_cancel(self):
        self._check_responsible()
        for rec in self:
            if rec.state not in ("draft", "processed"):
                raise UserError(
                    _("Only draft or processed SPMS returns can be cancelled.")
                )
            rec.state = "cancel"
        return True

    def write(self, vals):
        if "file" in vals or "period" in vals:
            for rec in self:
                if rec.state != "draft":
                    raise UserError(
                        _(
                            "The error file and period can only be changed on "
                            "a draft SPMS return."
                        )
                    )
        return super().write(vals)

    def unlink(self):
        for rec in self:
            if rec.state not in ("draft", "cancel"):
                raise UserError(
                    _("Only draft or cancelled SPMS returns can be deleted.")
                )
        return super().unlink()

    def action_create_credit_notes(self):
        self.ensure_one()
        self._check_responsible()
        self.invoice_ids._update_state()
        if self.state != "processed":
            raise UserError(
                _("Credit notes can only be generated from a processed " "SPMS return.")
            )
        ready_invoices = self.invoice_ids.filtered(lambda rec: rec.state == "ready")
        if not ready_invoices:
            raise UserError(
                _("There is no ready (green) invoice to generate a credit " "note for.")
            )
        generated = self.env["account.move"]
        blocked = []
        for invoice in ready_invoices:
            try:
                with self.env.cr.savepoint():
                    generated |= invoice._generate_credit_note()
            except UserError as error:
                blocked.append((invoice.display_name, str(error)))
        if not self.invoice_ids.filtered(
            lambda rec: rec.state not in ("done", "already_done")
        ):
            self.state = "done"
        blocked_summary = "\n".join(
            "- %s: %s" % (name, reason) for name, reason in blocked
        )
        if not generated:
            raise UserError(
                _("No credit note could be generated:\n%s") % blocked_summary
            )
        message = _("%d draft credit note(s) created.") % len(generated)
        if blocked:
            message += "\n" + _("%d invoice(s) could not be generated:\n%s") % (
                len(blocked),
                blocked_summary,
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "warning" if blocked else "success",
                "title": _("SPMS credit notes"),
                "message": message,
                "sticky": bool(blocked),
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": "spms.return",
                    "res_id": self.id,
                    "views": [(False, "form")],
                },
            },
        }

    def action_view_invoices(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("SPMS Return Invoices"),
            "res_model": "spms.return.invoice",
            "view_mode": "tree,form",
            "domain": [("return_id", "=", self.id)],
        }

    def _check_responsible(self):
        if not self.env.user.has_group(
            "l10n_pt_invoice_spms_return.spms_return_group_responsible"
        ):
            raise AccessError(_("Only SPMS Responsible users can perform this action."))

    def _check_can_process(self):
        self.ensure_one()
        self._check_responsible()
        if self.state not in ("draft", "processed"):
            raise UserError(
                _("SPMS return %s cannot be processed in its current state.")
                % self.display_name
            )
        if not self.file:
            raise UserError(
                _("Upload the error file before processing %s.") % self.display_name
            )

    def _parse_error_file(self):
        self.ensure_one()
        try:
            workbook = openpyxl.load_workbook(
                io.BytesIO(base64.b64decode(self.file)),
                read_only=True,
                data_only=True,
            )
        except Exception as error:
            raise UserError(
                _("Cannot read the error file as an Excel workbook: %s") % error
            ) from error
        rows_iter = workbook.active.iter_rows(values_only=True)
        header_row = next(rows_iter, None)
        if header_row is None:
            raise UserError(_("The error file is empty."))
        col_index = self._parse_error_file_headers(header_row)
        rows = []
        divergence_count = 0
        for row_number, row in enumerate(rows_iter, start=2):
            vals = self._parse_error_file_row(row, row_number, col_index)
            if vals is None:
                continue
            if vals["diverged"]:
                divergence_count += 1
            rows.append(vals)
        if not rows:
            raise UserError(_("The error file contains no data rows."))
        if divergence_count:
            _logger.warning(
                "SPMS return %s: %d rows where VALORTOTALAPURADOIVA differs "
                "from round(VALORTOTALAPURADO x 1.06); keeping the reported "
                "values.",
                self.display_name,
                divergence_count,
            )
        return rows

    @api.model
    def _parse_error_file_headers(self, header_row):
        col_index = {}
        for index, header in enumerate(header_row):
            if isinstance(header, str):
                col_index.setdefault(header.strip(), index)
        missing = [header for header in ERROR_FILE_HEADERS if header not in col_index]
        if missing:
            raise UserError(
                _("Missing expected columns in the error file: %s") % ", ".join(missing)
            )
        return col_index

    @api.model
    def _parse_error_file_row(self, row, row_number, col_index):
        invoice_number = _cell_text(_cell_value(row, col_index, "NUMFACTURA"))
        prescription = _cell_text(_cell_value(row, col_index, "NUMEROPRESCRICAO"))
        error_code = _cell_text(_cell_value(row, col_index, "COD_ERRO"))
        if not invoice_number and not prescription and not error_code:
            return None
        if not invoice_number:
            raise UserError(
                _("Row %d of the error file has no invoice number.") % row_number
            )
        amount_allowed = _cell_amount(_cell_value(row, col_index, "VALORTOTALAPURADO"))
        amount_allowed_taxed = _cell_amount(
            _cell_value(row, col_index, "VALORTOTALAPURADOIVA")
        )
        diverged = (
            float_compare(
                amount_allowed_taxed,
                float_round(amount_allowed * SPMS_TAX_FACTOR, precision_digits=2),
                precision_digits=2,
            )
            != 0
        )
        # an empty amount cell is missing data, not a real 0.00
        missing_amount = any(
            _cell_value(row, col_index, header) in (None, "")
            for header in ("VALORTOTAL", "VALORTOTALAPURADO", "VALORTOTALAPURADOIVA")
        )
        return {
            "invoice_number": invoice_number,
            "prescription": prescription,
            "amount_billed": _cell_amount(_cell_value(row, col_index, "VALORTOTAL")),
            "amount_allowed": amount_allowed,
            "amount_allowed_taxed": amount_allowed_taxed,
            "days_billed": _cell_amount(_cell_value(row, col_index, "QUANTIDADETOTAL")),
            "days_paid": _cell_amount(
                _cell_value(row, col_index, "Numero de dias pagos")
            ),
            "error_code": error_code,
            "error_description": _cell_text(_cell_value(row, col_index, "DESC_ERRO")),
            "provider_system_ref": _cell_text(
                _cell_value(row, col_index, "SISTEMAPRESTADOCRD")
            ),
            "excel_row": row_number,
            "diverged": diverged,
            "missing_amount": missing_amount,
        }

    def _snapshot_children(self):
        self.ensure_one()
        invoice_snapshot = {}
        line_snapshot = {}
        for invoice in self.invoice_ids:
            invoice_snapshot[invoice.name] = {
                "credit_official": invoice.credit_official,
                "official_confirmed": invoice.official_confirmed,
                "official_source": invoice.official_source,
                "official_date": invoice.official_date,
                "credit_note_move_id": invoice.credit_note_move_id.id,
                "done": invoice.state == "done",
            }
            for line in invoice.line_ids:
                if line.resolution or line.refund_move_line_id:
                    line_snapshot[(invoice.name, line.prescription)] = {
                        "resolution": line.resolution,
                        "refund_move_line_id": line.refund_move_line_id.id,
                    }
        return {"invoices": invoice_snapshot, "lines": line_snapshot}

    def _get_spms_move_map(self):
        self.ensure_one()
        moves = self.env["account.move"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
            ]
        )
        move_map = {}
        ambiguous = set()
        for move in moves:
            number = move._get_spms_invoice_number()
            if number in move_map:
                ambiguous.add(number)
            else:
                move_map[number] = move
        for number in ambiguous:
            move_map[number] = self.env["account.move"]
        if ambiguous:
            _logger.warning(
                "SPMS return %s: %d SPMS invoice numbers resolve to more "
                "than one invoice; left unmatched: %s",
                self.display_name,
                len(ambiguous),
                ", ".join(sorted(ambiguous)),
            )
        return move_map

    def _get_previous_lines_map(self, invoice_numbers):
        self.ensure_one()
        previous_lines = self.env["spms.return.invoice.line"].search(
            [
                (
                    "return_invoice_id.return_id.company_id",
                    "=",
                    self.company_id.id,
                ),
                ("return_invoice_id.return_id", "!=", self.id),
                (
                    "return_invoice_id.return_id.state",
                    "in",
                    ["processed", "done"],
                ),
                ("return_invoice_id.name", "in", invoice_numbers),
                ("prescription", "!=", False),
            ]
        )
        previous_map = {}
        for line in previous_lines.sorted(
            key=lambda rec: (rec.return_invoice_id.return_id.period, rec.id),
            reverse=True,
        ):
            previous_map.setdefault(
                (line.return_invoice_id.name, line.prescription), line.id
            )
        return previous_map

    def _create_children(self, rows, snapshot):
        self.ensure_one()
        grouped = {}
        for row in rows:
            invoice_group = grouped.setdefault(row["invoice_number"], {})
            line_key = row["prescription"] or "__row_%d" % row["excel_row"]
            invoice_group.setdefault(line_key, []).append(row)

        move_map = self._get_spms_move_map()
        matched_move_ids = [
            move.id for number, move in move_map.items() if move and number in grouped
        ]
        move_lines = self.env["account.move.line"].search(
            [
                ("move_id", "in", matched_move_ids),
                ("spms_prescription", "!=", False),
            ]
        )
        move_line_map = {}
        for move_line in move_lines:
            move_line_map.setdefault(
                (move_line.move_id.id, move_line.spms_prescription), []
            ).append(move_line.id)

        previous_map = self._get_previous_lines_map(list(grouped.keys()))

        invoice_vals_list = []
        for invoice_number in grouped:
            move = move_map.get(invoice_number)
            snap = snapshot["invoices"].get(invoice_number, {})
            vals = {
                "return_id": self.id,
                "name": invoice_number,
                "move_id": move.id if move else False,
                "credit_official": snap.get("credit_official", 0.0),
                "official_confirmed": snap.get("official_confirmed", False),
                "official_source": snap.get("official_source", False),
                "official_date": snap.get("official_date", False),
            }
            if snap.get("done"):
                vals["state"] = "done"
                vals["credit_note_move_id"] = snap.get("credit_note_move_id", False)
            invoice_vals_list.append(vals)
        invoices = self.env["spms.return.invoice"].create(invoice_vals_list)

        line_vals_list = []
        line_rows = []
        for invoice, invoice_number in zip(invoices, grouped):
            move = move_map.get(invoice_number)
            for group_rows in grouped[invoice_number].values():
                first = group_rows[0]
                prescription = first["prescription"]
                incoherent = any(
                    float_compare(
                        row["amount_billed"],
                        first["amount_billed"],
                        precision_digits=2,
                    )
                    != 0
                    or float_compare(
                        row["amount_allowed"],
                        first["amount_allowed"],
                        precision_digits=2,
                    )
                    != 0
                    for row in group_rows[1:]
                )
                candidate_ids = (
                    move_line_map.get((move.id, prescription), [])
                    if move and prescription
                    else []
                )
                if (
                    incoherent
                    or any(row["missing_amount"] for row in group_rows)
                    or any(row["diverged"] for row in group_rows)
                ):
                    state = "data_error"
                elif not candidate_ids:
                    state = "not_found"
                elif len(candidate_ids) > 1:
                    state = "ambiguous"
                elif float_is_zero(
                    first["amount_billed"] - first["amount_allowed"],
                    precision_digits=2,
                ):
                    state = "zero_diff"
                else:
                    state = "matched"
                line_snap = snapshot["lines"].get((invoice_number, prescription), {})
                line_vals_list.append(
                    {
                        "return_invoice_id": invoice.id,
                        "prescription": prescription,
                        "amount_billed": first["amount_billed"],
                        "amount_allowed": first["amount_allowed"],
                        "amount_allowed_taxed": first["amount_allowed_taxed"],
                        "days_billed": first["days_billed"],
                        "days_paid": first["days_paid"],
                        "state": state,
                        "move_line_id": (
                            candidate_ids[0] if len(candidate_ids) == 1 else False
                        ),
                        "previous_line_id": previous_map.get(
                            (invoice_number, prescription), False
                        ),
                        "resolution": line_snap.get("resolution", False),
                        "refund_move_line_id": line_snap.get(
                            "refund_move_line_id", False
                        ),
                    }
                )
                line_rows.append(group_rows)
        lines = self.env["spms.return.invoice.line"].create(line_vals_list)

        error_vals_list = []
        for line, group_rows in zip(lines, line_rows):
            for row in group_rows:
                error_vals_list.append(
                    {
                        "line_id": line.id,
                        "code": row["error_code"],
                        "description": row["error_description"],
                        "provider_system_ref": row["provider_system_ref"],
                        "excel_row": row["excel_row"],
                    }
                )
        self.env["spms.return.invoice.line.error"].create(error_vals_list)
