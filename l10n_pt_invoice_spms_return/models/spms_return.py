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

AMOUNT_HEADERS = (
    "VALORTOTAL",
    "VALORTOTALAPURADO",
    "VALORTOTALAPURADOIVA",
    "QUANTIDADETOTAL",
    "Numero de dias pagos",
)
# money columns; the day counts may legitimately come empty
REQUIRED_AMOUNT_HEADERS = (
    "VALORTOTAL",
    "VALORTOTALAPURADO",
    "VALORTOTALAPURADOIVA",
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
    """None when the cell value cannot be read as an amount."""
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return None


class SpmsReturn(models.Model):
    _name = "spms.return"
    _description = "SPMS Return"
    _inherit = ["mail.thread"]
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
            ("imported", "Imported"),
            ("linked", "Linked"),
            ("cancel", "Cancelled"),
        ],
        string="State",
        required=True,
        readonly=True,
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

    def action_import(self):
        for rec in self:
            rec._check_can_import()
            rows = rec._parse_error_file()
            # keep human input before rebuilding the children
            snapshot = rec._snapshot_children()
            rec.invoice_ids.with_context(spms_return_reprocess=True).unlink()
            rec._create_children(rows, snapshot)
            rec.state = "imported"
        return True

    def action_link(self):
        for rec in self:
            rec._check_can_link()
            rec._link_children()
            rec.invoice_ids._update_state()
            rec.state = "linked"
        return True

    def action_back_to_draft(self):
        self._check_responsible()
        for rec in self:
            if rec.state not in ("imported", "linked", "cancel"):
                raise UserError(
                    _(
                        "Only imported, linked or cancelled SPMS returns can "
                        "be set back to draft."
                    )
                )
            rec.state = "draft"
        return True

    def action_cancel(self):
        self._check_responsible()
        for rec in self:
            if rec.state not in ("draft", "imported", "linked"):
                raise UserError(
                    _(
                        "Only draft, imported or linked SPMS returns can be "
                        "cancelled."
                    )
                )
            rec.state = "cancel"
        return True

    def write(self, vals):
        if "file" in vals or "period" in vals or "date" in vals:
            for rec in self:
                if rec.state != "draft":
                    raise UserError(
                        _(
                            "The error file, period and date can only be "
                            "changed on a draft SPMS return."
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
        if self.state != "linked":
            raise UserError(
                _("Credit notes can only be generated from a linked " "SPMS return.")
            )
        self.invoice_ids._update_state()
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

    def _check_can_import(self):
        self.ensure_one()
        self._check_responsible()
        if self.state not in ("draft", "imported", "linked"):
            raise UserError(
                _("SPMS return %s cannot be imported in its current state.")
                % self.display_name
            )
        if not self.file:
            raise UserError(
                _("Upload the error file before importing %s.") % self.display_name
            )

    def _check_can_link(self):
        self.ensure_one()
        self._check_responsible()
        if self.state not in ("imported", "linked"):
            raise UserError(
                _("SPMS return %s must be imported before linking.") % self.display_name
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
        for row_number, row in enumerate(rows_iter, start=2):
            vals = self._parse_error_file_row(row, row_number, col_index)
            if vals is None:
                continue
            rows.append(vals)
        workbook.close()
        if not rows:
            raise UserError(_("The error file contains no data rows."))
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
        amounts, amount_reason = self._row_amounts(row, col_index)
        return {
            "invoice_number": invoice_number,
            "prescription": prescription,
            "amount_billed": amounts["VALORTOTAL"],
            "amount_allowed": amounts["VALORTOTALAPURADO"],
            "amount_allowed_taxed": amounts["VALORTOTALAPURADOIVA"],
            "days_billed": amounts["QUANTIDADETOTAL"],
            "days_paid": amounts["Numero de dias pagos"],
            "error_code": error_code,
            "error_description": _cell_text(_cell_value(row, col_index, "DESC_ERRO")),
            "provider_system_ref": _cell_text(
                _cell_value(row, col_index, "SISTEMAPRESTADOCRD")
            ),
            "excel_row": row_number,
            "diverged": False,
            "amount_reason": amount_reason,
        }

    @api.model
    def _row_amounts(self, row, col_index):
        """Read the amount cells of a row, flagging empty or unconvertible
        ones."""
        amounts = {}
        reason = None
        for header in AMOUNT_HEADERS:
            raw = _cell_value(row, col_index, header)
            value = _cell_amount(raw)
            if value is None:
                reason = "unconvertible"
            elif header in REQUIRED_AMOUNT_HEADERS and raw in (None, "") and not reason:
                reason = "missing"
            amounts[header] = value or 0.0
        return amounts, reason

    @api.model
    def _group_data_error_reason(self, group_rows, incoherent):
        """Why this line group is a data error, or False if it is not.

        Unreadable cells come first: a divergence or a contradiction
        computed over unreadable amounts is derived noise.
        """
        if any(row["amount_reason"] == "unconvertible" for row in group_rows):
            return "unconvertible"
        if any(row["amount_reason"] == "missing" for row in group_rows):
            return "missing"
        if any(row["diverged"] for row in group_rows):
            return "diverged"
        if incoherent:
            return "incoherent"
        return False

    @api.model
    def _line_state(self, data_error_reason, candidate_ids, line):
        # worst state wins:
        # data_error > not_found > ambiguous > zero_diff > matched
        if data_error_reason:
            return "data_error"
        if not candidate_ids:
            return "not_found"
        if len(candidate_ids) > 1:
            return "ambiguous"
        if float_is_zero(
            line.amount_billed - line.amount_allowed,
            precision_digits=2,
        ):
            return "zero_diff"
        return "matched"

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
                if line.refund_move_line_id:
                    line_snapshot[(invoice.name, line.prescription)] = {
                        "refund_move_line_id": line.refund_move_line_id.id,
                    }
        return {"invoices": invoice_snapshot, "lines": line_snapshot}

    def _get_spms_move_map(self, invoice_numbers):
        self.ensure_one()
        moves = self.env["account.move"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
            ]
        )
        move_map = {}
        collisions = {}
        for move in moves:
            number = move._get_spms_invoice_number()
            if number in move_map:
                collisions.setdefault(number, [move_map[number]]).append(move)
            else:
                move_map[number] = move
        blocked = sorted(set(collisions) & set(invoice_numbers))
        if blocked:
            raise UserError(
                _(
                    "The following SPMS invoice numbers match more than one "
                    "posted invoice: %s. Fix the duplicated invoices before "
                    "linking the return."
                )
                % "; ".join(
                    "%s (%s)"
                    % (
                        number,
                        ", ".join(move.display_name for move in collisions[number]),
                    )
                    for number in blocked
                )
            )
        for number in collisions:
            move_map[number] = self.env["account.move"]
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
                # any non-cancelled return claims its pairs, a draft one
                # included: only cancelling frees them (§8.15)
                ("return_invoice_id.return_id.state", "!=", "cancel"),
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
        """Rebuild the children from the parsed rows: PURE parser side.

        No Odoo lookup happens here — links, semaphores and the tax canary
        belong to the link step. Human input survives the rebuild through
        the snapshot.
        """
        self.ensure_one()
        grouped = {}
        for row in rows:
            invoice_group = grouped.setdefault(row["invoice_number"], {})
            line_key = row["prescription"] or "__row_%d" % row["excel_row"]
            invoice_group.setdefault(line_key, []).append(row)

        invoice_vals_list = []
        for invoice_number in grouped:
            snap = snapshot["invoices"].get(invoice_number, {})
            vals = {
                "return_id": self.id,
                "name": invoice_number,
                "credit_official": snap.get("credit_official", 0.0),
                "official_confirmed": snap.get("official_confirmed", False),
                "official_source": snap.get("official_source", False),
                "official_date": snap.get("official_date", False),
            }
            if snap.get("done"):
                vals["state"] = "done"
                vals["credit_note_move_id"] = snap.get("credit_note_move_id", False)
            invoice_vals_list.append(vals)
        # create() returns records in the same order as vals
        invoices = self.env["spms.return.invoice"].create(invoice_vals_list)

        line_vals_list = []
        line_rows = []
        for invoice, invoice_number in zip(invoices, grouped):
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
                    or float_compare(
                        row["amount_allowed_taxed"],
                        first["amount_allowed_taxed"],
                        precision_digits=2,
                    )
                    != 0
                    for row in group_rows[1:]
                )
                data_error_reason = self._group_data_error_reason(
                    group_rows, incoherent
                )
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
                        "state": "data_error" if data_error_reason else False,
                        "data_error_reason": data_error_reason,
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

    def _link_children(self):
        """Match the imported tables against Odoo: LINK side.

        Reads only the children and accounting, writes links and line
        semaphores. Deletes nothing and never touches human input, so
        linking and re-linking are the same idempotent operation.
        """
        self.ensure_one()
        invoices = self.invoice_ids
        invoice_numbers = [name for name in invoices.mapped("name") if name]
        move_map = self._get_spms_move_map(invoice_numbers)

        for invoice in invoices:
            move = move_map.get(invoice.name)
            invoice.move_id = move.id if move else False

        move_lines = self.env["account.move.line"].search(
            [
                ("move_id", "in", invoices.move_id.ids),
                ("spms_prescription", "!=", False),
            ]
        )
        move_line_map = {}
        for move_line in move_lines:
            move_line_map.setdefault(
                (move_line.move_id.id, move_line.spms_prescription), []
            ).append(move_line.id)

        previous_map = self._get_previous_lines_map(invoice_numbers)

        divergence_count = 0
        for invoice in invoices:
            for line in invoice.line_ids:
                candidate_ids = (
                    move_line_map.get((invoice.move_id.id, line.prescription), [])
                    if invoice.move_id and line.prescription
                    else []
                )
                # the link step owns the 'diverged' verdict; the parse-side
                # reasons are never touched
                data_error_reason = line.data_error_reason
                if data_error_reason == "diverged":
                    data_error_reason = False
                if not data_error_reason and len(candidate_ids) == 1:
                    factor = line._get_tax_factor(
                        self.env["account.move.line"].browse(candidate_ids[0]).tax_ids
                    )
                    if (
                        float_compare(
                            line.amount_allowed_taxed,
                            float_round(
                                line.amount_allowed * factor,
                                precision_digits=2,
                            ),
                            precision_digits=2,
                        )
                        != 0
                    ):
                        data_error_reason = "diverged"
                        divergence_count += 1
                line.write(
                    {
                        "move_line_id": (
                            candidate_ids[0] if len(candidate_ids) == 1 else False
                        ),
                        "previous_line_id": previous_map.get(
                            (invoice.name, line.prescription), False
                        ),
                        "data_error_reason": data_error_reason,
                        "state": self._line_state(
                            data_error_reason, candidate_ids, line
                        ),
                    }
                )
        if divergence_count:
            _logger.warning(
                "SPMS return %s: %d lines where VALORTOTALAPURADOIVA differs "
                "from VALORTOTALAPURADO with the invoice line tax applied; "
                "keeping the reported values.",
                self.display_name,
                divergence_count,
            )
