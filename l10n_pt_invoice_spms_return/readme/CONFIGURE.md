- Assign the *SPMS / Consultation* group to users who can read the SPMS
  returns, and *SPMS / Responsible* to the users allowed to process files
  and generate credit notes.
- Set the *SPMS Adjustment Line Product* on the company (SPMS page, visible
  to *Technical Settings* users): a sale service product required to append
  the adjustment line when an official value differs from the itemised
  total. Credit notes that need it are skipped with a clear message until
  it is configured. Its sale tax is also the fallback rate for the with-VAT
  credit estimate of lines not matched to an invoice line; matched lines
  always use their own invoice line tax.
- The importer requires the `openpyxl` Python library on the server.
