This module imports the monthly SPMS/CCMSNS conference error report
("Conferência") into Odoo and manages the resulting rectifying credit notes:

- Imports the SNS billing-error Excel and stores the full error log
  (return → invoice → prescription → error), including non-economic errors.
- Computes the estimated credit per prescription and per invoice following the
  validated conference criterion.
- Tracks the official credit-note value per invoice and gates the generation
  on its confirmation.
- Generates draft rectifying invoices (credit notes) natively linked to the
  original invoices through the standard reversal mechanism.
