"""Polite fetcher + classifier for real-world government PDF forms.

We have exactly one hand-verified real form (fixtures/safer.pdf): produced by
"Microsoft Word for Microsoft 365", full of thin-rect table borders,
Webdings/Wingdings checkbox glyphs and underscore write-on lines. Adobe
LiveCycle Designer forms (checked: CRA T2201, IRS W-9) have none of that
structure -- they build fields with a real AcroForm instead. This module
fetches public government PDFs and classifies each one by how much it looks
like safer.pdf, so the "flat-wordlike" ones can become hand-labelled ground
truth.

    def fetch(urls, out_dir, limit=60) -> dict     # the manifest
    def classify(pdf_path) -> dict                 # one file's record

CLI:
    python -m eval.fetch --out eval/corpus/real [--limit 60]

classify() takes no network access and never raises -- on anything it cannot
parse it returns verdict "unusable" with a "reason" string.

Politeness, all non-negotiable because this runs unattended:
  - robots.txt is honoured (urllib.robotparser), per host, fetched once.
  - one request at a time per host, >=2s between requests to the same host.
  - a fixed, identifying User-Agent.
  - cached by URL and by content sha256 -- a URL or hash already in the
    manifest is never re-fetched.
  - hard caps: 60 files/run, 20 MB/file, 30 pages/file.
  - every request is timed out; at most 2 retries, with backoff, and only for
    transient errors. A 403 or 429 stops all further fetching from that host
    for the rest of the run.
"""
import argparse
import hashlib
import json
import signal
import socket
import time
import urllib.error
import urllib.request
import urllib.robotparser
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pdfplumber
import pypdf

USER_AGENT = (
    "FormFill-research/0.1 "
    "(+PDF form accessibility research; contact ryan.xu282@gmail.com)"
)

REQUEST_TIMEOUT = 20            # seconds, every request
MIN_HOST_DELAY = 2.0            # seconds, minimum gap between requests to one host
MAX_RETRIES = 2                 # retries after the first attempt (never more than 2)
RETRY_BACKOFF = 2.0             # seconds; attempt n waits n * RETRY_BACKOFF

MAX_FILES = 60                  # hard cap: files fetched in one run
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PAGES = 30
CLASSIFY_TIMEOUT_SECONDS = 20   # safety net against pathological PDFs

CHECK_GLYPHS = {"", ""}   # Webdings box, Wingdings box (same as engine/detect/rules.py)

VERDICTS = (
    "flat-wordlike", "flat-sparse", "fillable-livecycle",
    "fillable-other", "scan", "unusable",
)

# Candidate direct-PDF links on official government domains, gathered by
# searching each domain for downloadable application forms. Not all of these
# will turn out to be flat-wordlike (that is the whole point of classifying
# them) -- some are LiveCycle AcroForms, some are scans. The fetcher records
# whatever it finds, including failures.
SEED_URLS = [
    # gov.bc.ca -- Residential Tenancy Branch forms
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb1_chrome.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb1c.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb2.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb10.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12tpt.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12tct.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12texh.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12tdr.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12lct.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb13.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb51.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb52.pdf",
    # gov.bc.ca -- BC Employment and Assistance forms
    "https://www2.gov.bc.ca/assets/gov/british-columbians-our-governments/policies-for-government/bc-employment-assistance-policy-procedure-manual/forms/pdfs/hr2883.pdf",
    "https://www2.gov.bc.ca/assets/gov/british-columbians-our-governments/policies-for-government/bc-employment-assistance-policy-procedure-manual/forms/pdfs/hr2847.pdf",
    # alberta.ca -- assorted program application forms
    "https://www.alberta.ca/system/files/custom_downloaded_images/tr-tnc-application-form.pdf",
    "https://www.alberta.ca/system/files/custom_downloaded_images/scss-sfa-application-form.pdf",
    "https://www.alberta.ca/system/files/ag-sample-ofep-application-form.pdf",
    "https://www.alberta.ca/system/files/custom_downloaded_images/ahcip-rrnp-flat-fee-application-form.pdf",
    "https://www.alberta.ca/system/files/custom_downloaded_images/CARES-sample-application-form.pdf",
    "https://www.alberta.ca/system/files/custom_downloaded_images/agi-scap-water-program-application-form.pdf",
    "https://www.alberta.ca/system/files/pses-homeowner-tenant-harp-application-form.pdf",
    # ontario.ca -- program application forms
    "https://www.ontario.ca/files/2026-01/rural-ontario-development-community-development-application-en-2026-01-16.pdf",
    "https://www.ontario.ca/files/2025-06/mra-rod-business-development-application-form-en-2025-06-23.pdf",
    "https://www.ontario.ca/files/2024-05/moh-information-guide-application-for-psychiatric-assessment-form-1-en-2024-05-21.pdf",
    "https://files.ontario.ca/mccss-autism-workforce-capacity-fund-sector-innovation-application-form-en-2020-08-10.pdf",
    # canada.ca -- ESDC / student loan forms
    "https://www.canada.ca/content/dam/canada/employment-social-development/migration/documents/assets/portfolio/docs/en/student_loans/forms/SDE0031_EN.pdf",
    "https://www.canada.ca/content/dam/canada/employment-social-development/migration/documents/assets/portfolio/docs/en/student_loans/forms/confirmation_posting-en.pdf",
    "https://www.canada.ca/content/dam/canada/employment-social-development/services/funding/canada-summer-jobs/ESDC-EMP5616_EN.pdf",
    # canada.ca / ircc.canada.ca -- immigration forms
    "https://ircc.canada.ca/english/pdf/kits/forms/IMM5918E.pdf",
    "https://ircc.canada.ca/english/pdf/kits/forms/imm0008egen.pdf",
    "https://www.canada.ca/content/dam/ircc/migration/ircc/english/passport/forms/pdf/pptc190.pdf",
    "https://www.canada.ca/content/dam/ircc/migration/ircc/english/pdf/kits/forms/imm5280e.pdf",
    "https://www.canada.ca/content/dam/ircc/migration/ircc/english/pdf/kits/forms/imm5475e.pdf",
    # ssa.gov -- benefit application forms
    "https://www.ssa.gov/forms/ss-5.pdf",
    "https://www.ssa.gov/forms/ssa-8.pdf",
    "https://www.ssa.gov/forms/ssa-16-bk.pdf",
    "https://www.ssa.gov/forms/ssa-1696.pdf",
    "https://www.ssa.gov/forms/ssa-1-bk.pdf",
    "https://www.ssa.gov/forms/ssa-2-bk.pdf",
    "https://www.ssa.gov/forms/ssa-2490-bk.pdf",
    "https://www.ssa.gov/forms/ss-5fs.pdf",
    "https://www.ssa.gov/legislation/medicare/Part_D_application.pdf",

    # --- expansion: more Canadian provinces/territories -------------------
    # gov.mb.ca -- Manitoba
    "https://vitalstats.gov.mb.ca/pdf/application_mb_birth_document.en.pdf",
    "https://www.gov.mb.ca/health/ems/forms/air_application.pdf",
    "https://www.gov.mb.ca/fs/eia/pubs/mcb_application.pdf",
    # novascotia.ca / parl.ns.ca -- Nova Scotia
    "https://novascotia.ca/finance/PDFs/Form12-Financial-Hardship-Application-Revised.pdf",
    "http://www.parl.ns.ca/projects/healthroom/pdf/applications/application-to-director.pdf",
    "https://liveinnovascotia.com/sites/default/files/2024-05/NSNP-200-Employer-Information-English.pdf",
    # gnb.ca -- New Brunswick
    "https://www2.gnb.ca/content/dam/gnb/Gateways/ABCs/ABC_application_form-e.pdf",
    "https://www2.gnb.ca/content/dam/gnb/Departments/ag-pg/PDF/Forms/FORM-fillable-81a-b.pdf",
    "https://www2.gnb.ca/content/dam/gnb/Departments/ag-pg/PDF/Forms/FORM-07a.pdf",
    "https://www2.gnb.ca/content/dam/gnb/Departments/ag-pg/PDF/Forms/FORM-72f-e.pdf",
    # gov.nl.ca -- Newfoundland and Labrador (Digital Government Services forms directory)
    "https://www.gov.nl.ca/gs/files/forms-pdf-appl-cert-plant-registration.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-appl-contract-licence.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-contractors-specifications-registration-pressure-piping-systems.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-appl-examination-medical-gas-installer.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-appl-lp-gas-plant-licence.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-appl-propane-gas-install-exam.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-appl-permit-install-alter-pressure-system.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-appl-certificate-proficiency.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-reg-pressure-piping-systems.pdf",
    "https://www.gov.nl.ca/gs/files/licenses-building-appl-building-registration.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-ele-cont-insp-rep.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-appl-electrical-maintenance-permit.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-electrical-permit.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-appl-electrical-contractors-renewal_01-21.pdf",
    "https://www.gov.nl.ca/gs/files/specification-sheet-for-amusement-rides-and-elevating-devices.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-appl-asphalt-plant.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-appl-cert-approval.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-livestock-poultry-farm-coa-application-rev-1.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-appl-storage-tank-system.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-appl-storage-tank-system-used.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-mobile-fuel-storage-tank-relocation-form.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-sts-test-form.pdf",
    "https://www.gov.nl.ca/gs/files/Application-For-The-Establishment-of-Fuel-Caches-At-Remote-Sites.pdf",
    "https://www.gov.nl.ca/gs/files/Application-For-The-Establishment-of-Fuel-Caches-At-Non-Remote-Sites.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-nbcc-long-form.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-nbcc-short-form.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-app-food-tobacco-lic.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-appl-temporary-food-est.pdf",
    "https://www.gov.nl.ca/gs/files/licenses-env-health-pdf-personal-services-form.pdf",
    "https://www.gov.nl.ca/gs/files/licenses-highway-off-site-new-appl-erect-signs.pdf",
    "https://www.gov.nl.ca/gs/files/licenses-highway-off-site-highwaysignagenotice.pdf",
    "https://www.gov.nl.ca/gs/files/licenses-highway-fingerbd-appl-highwaysigns.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-all-species-order.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-all-species-refund.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-vendor-form.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-affidavit-lossofsmallgamelic.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-preliminaryappltodevland-web.pdf",
    "https://www.gov.nl.ca/gs/files/forms-pdf-application-develop-land-cemetary-site-fillable.pdf",
    "https://www.gov.nl.ca/gs/files/landlord-dispute-resolution.pdf",
    "https://www.gov.nl.ca/gs/files/landlord-term-notice-to-landlord.pdf",
    "https://www.gov.nl.ca/gs/files/landlord-landlords-notice-to-terminate-standard.pdf",

    # --- expansion: US states -- courts, DMV, health/human services --------
    # dmv.ny.gov -- New York DMV forms
    "https://dmv.ny.gov/forms/mv202c.pdf",
    "https://dmv.ny.gov/forms/mv349.pdf",
    "https://dmv.ny.gov/forms/mv3491.pdf",
    "https://dmv.ny.gov/forms/mv253g.pdf",
    "https://dmv.ny.gov/forms/mv327.pdf",
    "https://dmv.ny.gov/forms/mv278faqs.pdf",
    "https://dmv.ny.gov/forms/mv2994.pdf",
    "https://dmv.ny.gov/forms/mv37.pdf",
    "https://dmv.ny.gov/forms/mv2786.pdf",
    "https://dmv.ny.gov/forms/mv2788ssc.pdf",
    "https://dmv.ny.gov/forms/mv232.pdf",
    "https://dmv.ny.gov/forms/mv35.pdf",
    # txcourts.gov -- Texas court forms
    "https://www.txcourts.gov/media/847145/expedited-foreclosure-forms-for-website.pdf",
    "https://www.txcourts.gov/media/1456942/statement-of-inability-to-afford-payment-of-court-costs-or-an-appeal-bond-bilingual.pdf",
    "https://www.txcourts.gov/media/1454132/notice-of-protected-property-rights-bilingual.pdf",
    "https://www.txcourts.gov/media/1454131/instructions-for-claim-form-bilingual.pdf",
    "https://www.txcourts.gov/media/1454133/claim-form-bilingual.pdf",
    "https://www.txcourts.gov/media/1454134/form-receiver-order.pdf",
    "https://www.txcourts.gov/media/1456198/20230307-application-and-petition-to-stop-cyberbullying-integrated-and-fillable.pdf",
    "https://www.txcourts.gov/media/1456662/will-unmarried-w-children-english.pdf",
    "https://www.txcourts.gov/media/1456664/will-married-w-children-english.pdf",
    "https://www.txcourts.gov/media/1456663/will-unmarried-w-no-children-english.pdf",
    "https://www.txcourts.gov/media/1456661/will-married-w-no-children-english.pdf",
    "https://www.txcourts.gov/media/1456660/will-unmarried-w-children-bilingual.pdf",
    "https://www.txcourts.gov/media/1456653/will-married-w-children-bilingual.pdf",
    "https://www.txcourts.gov/media/1456658/will-unmarried-w-no-children-bilingual.pdf",
    "https://www.txcourts.gov/media/1456659/will-married-w-no-children-bilingual.pdf",
    "https://www.txcourts.gov/media/1461453/instructions-for-felony-judgment-forms-10-24-2025.pdf",
    "https://www.txcourts.gov/media/1461136/affirmative-findings-and-special-orders-for-felony-judgment-form-09012025.pdf",
    "https://www.txcourts.gov/media/518971/templatecompetencyeval.pdf",
    "https://www.txcourts.gov/media/1443019/instructions-for-notice-of-judicial-clemency-and-discharge-order.pdf",
    "https://www.txcourts.gov/media/1453404/reconsideration-of-ability-to-pay-form.pdf",
    "https://www.txcourts.gov/media/1449765/model-oral-admonishment-language.pdf",
    "https://www.txcourts.gov/media/1449764/model-written-admonishment-language.pdf",
    "https://www.txcourts.gov/media/515764/divorceset1forms.pdf",
    "https://www.txcourts.gov/media/1454841/parental-notification-forms.pdf",
    "https://www.txcourts.gov/media/1457123/remote-detention-hearing-procedures-model-form.pdf",
    "https://www.txcourts.gov/media/1441652/self-help-notice.pdf",
    "https://www.txcourts.gov/media/1441651/self-help-notice-with-computer.pdf",
    "https://www.txcourts.gov/media/1461702/request-for-omission-or-redaction-of-certain-real-property-records.pdf",
    "https://www.txcourts.gov/media/1461146/2025-model-grand-jury-summons-form-pop-more-than-1000.pdf",
    "https://www.txcourts.gov/media/1461147/2025-model-grand-jury-summons-form-population-less-than-1000.pdf",
    "https://www.txcourts.gov/media/1461145/2025-model-grand-jury-summons-questionnaire-instructions.pdf",
    "https://www.txcourts.gov/media/1461144/permanent-jury-exemption-form-based-on-age-2025-version.pdf",
    "https://www.txcourts.gov/media/1461141/2025-model-petit-jury-summons-form-general-model-for-all-groups.pdf",
    "https://www.txcourts.gov/media/1461142/2025-model-petit-jury-summons-form-pop-less-than-1k.pdf",
    "https://www.txcourts.gov/media/1461143/2025-model-petit-jury-summons-form-pop-1k-to-200k.pdf",
    # illinoiscourts.gov -- Illinois court forms (served from Azure blob storage)
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/14bd93da-2189-4b0a-bf30-a19a224ceff6/LSA%20Notice.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/2b97ae8e-ec90-4aa7-9ff9-579a8b0055d4/LSA%20Notice%20of%20Withdrawal.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/84be618b-f884-43f6-a075-b6f90f03df5a/LSA%20Objection%20to%20Withdrawal.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/b26a9b62-cc01-4ab0-8186-5ae72b859ace/Rule_17_Foreign_Subpoena_Attestation.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/03a0bdb2-4857-48c9-aab3-447b2e2267a3/SUM%20Summons.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/526e90d3-59df-4d33-9995-020b898f4bc5/110.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/4a602d48-5dac-4de3-b675-f864f1eb4dab/280.2.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/a0996015-2b09-4135-b6cb-0306a6e08753/IDT%20Affidavit.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/0fa300fa-abab-41e1-bac2-f0cdc0f27255/291.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/c528ba84-894b-43f3-8390-12fe8e15bb79/292.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/52beec8c-25fc-4d0f-bc56-82a93b68d395/FW-CIV%20Application.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/cafd98d0-d326-4d82-ba64-3fb345702a3c/FW-AC%20Application.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/04489a99-9484-4ee2-9f97-9919fdc5e094/FW-SC%20Application.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/6d7b74c7-f005-4d2b-afef-d88678063cb9/Rule_298_Application_Waiver_Court_Fees_form.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/0a7de493-a2fe-4e5f-acd5-64abcc5457d3/DS%20Docketing%20Statement.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/b40debf4-0529-4b1c-8038-1231af8dc8f9/RRA%20Application.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/da298661-6e2a-4f5e-9a87-84f80072c1a8/FW-CRM%20Application.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/f5c72ba0-3f79-4fd8-a73f-a3a860bc5d1f/Rule_404_Application_Waiver_Court_Assements_form.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/0e5fff82-c135-4558-aae1-c0bb7345f0c2/Judgment_Sentence_to_DOC.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/cdba8fcd-aa1b-4206-a2af-7daf3f017c28/Notice%20of%20Pretrial%20Fairness%20Act%20Appeal%20604(h)%20(State%20as%20Appellant)_Fillable2.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/322f92da-e966-405c-9dc3-a118a6abdb05/Notice%20of%20Pretrial%20Fairness%20Act%20Appeal%20604(h)%20(Defendant%20as%20Appellant)_%20Fillable2.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/d2fda58b-c78c-4a4e-9262-a15f69fbd84f/Application-LAW_STUDENT.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/e1a8ef0a-4af9-43ba-9494-55325e443d06/Application-GRADUATE.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/c0587bd8-e254-4e29-a88e-6e76ef9a6682/Notice_Add_Chg_Employer.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/3ff13c1b-e15d-41c3-b718-6184a9b3a451/Rule%20711%20Notice_Name_Change.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/93a03075-fd3d-429e-9d9f-3f0a9649f420/Rule_Peition_Form_7.2c.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/44d08367-c172-4a8f-bcb7-041e042a71ba/112812_3.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/551392fe-f4d4-4efa-8ef7-3e6b128a93ce/FRO%20Summons.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/862dae42-a93d-4561-8e13-c372706e70be/FRO%20Seizure-Search%20Warrant.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/5c0c71cc-aefa-4c6e-98ab-5cc62125f314/FRO%20Petition.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/eed6a96f-92b0-40f1-8f5b-4e7ec8ed62be/FRO%20Plenary%20Order.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/7dfc7bd2-ec1d-4bb2-b16c-95a62ca84669/FRO%20Extension%20Order.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/1e5a8d23-2efa-4505-bc43-4893fd085a94/FRO%20Motion%20Terminate.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/1d270b7e-7fde-4483-846e-7f8d57865d71/FRO%20Emergency%20Order.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/424d3883-8d64-42e3-8372-2d51674dd584/Rule%20552.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/dcabeebc-f00b-4160-b6bb-6a6aefa7113f/Uniform%20Citation%20and%20Complaint.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/bc4e3e9a-6636-4e1c-b892-038ad17d8dd9/Electronic%20Citation%20and%20Complaint.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/e4f67cf8-d89c-4f66-ba2d-4064afa89630/Overweight%20Citation%20and%20Complaint.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/ba2f7735-52ad-46b7-a8d1-21348fbf5af0/Electronic%20Overweight%20Citation%20and%20Complaint.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/934ade14-19bb-46ae-9f28-5091cc22fbd2/Civil%20Law%20Citation%20and%20Complaint.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/1405d59c-3e42-4492-ad24-781f2486bbf7/Electronic%20Civil%20Law%20Citation%20and%20Complaint.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/34af3111-6fb8-40d8-97fe-d2f7b49f781c/Conservation%20Citation%20and%20Complaint.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/86016a11-4a5f-45a0-9b00-488eb60fa034/CNCO%20Petition.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/db750197-da3b-41fa-a78a-f74cf5d77386/CNCO%20Motion%20Extend%20or%20Modify.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/07a296fa-d4be-4caf-9b9b-c17e383dfcd3/CNCO%20Order%20Extend%20or%20Modify.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/3e2ddbe1-a490-410c-bdac-23d094aa3bf5/CNCO%20Order.pdf",
    "https://ilcourtsaudio.blob.core.windows.net/antilles-resources/resources/a8a66ba6-37ae-4336-9908-479fe5efda54/CNCO%20Summons.pdf",
    # dcyf.wa.gov -- Washington State Dept. of Children, Youth & Families
    "https://dcyf.wa.gov/sites/default/files/forms/ProviderRegistrationForm.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/ProviderChangeForm.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/ProviderDirectDeposit.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-003aSM.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-003aSP.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-026.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-027.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-028.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-031.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-040.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-040sm.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-040sp.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-206.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-206AL.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-206da.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-206pa.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-206ru.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-206SP.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/02-206vi.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/03-186.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/03-225.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/03-374B.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/03-415.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/03-492.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/03-493.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/04-220.pdf",
    "https://dcyf.wa.gov/sites/default/files/forms/04-220ti.pdf",

    # --- expansion: other English-speaking jurisdictions -------------------
    # transport.nsw.gov.au -- New South Wales, Australia (vehicle/licence forms)
    "https://tfnswforms.transport.nsw.gov.au/45062918-access-to-personal-records-app.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45071665-advice-of-death.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45070212-change-of-records.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45072029-classic-vehicle-declaration.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45070939-conditional-reg.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45071265-customer-number-application-private.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45071266-customer-number-application.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45061655-driving-instructor-licence-app.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45070803-good-behaviour-election.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45070967-historic-vehicle-declaration.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45062862-proof-of-registration.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45070787-replacement-learners-log-book-application.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45070018-licence-application.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45071506-licence-renewal-application.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45070182-replacement-application.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45071651-medical-condition-notification-form.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45071414-request-for-a-modified-licence.pdf",
    "https://tfnswforms.transport.nsw.gov.au/48013716-notice-of-disposal.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45072066-application-passenger-transport-licence-code.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45071143-provisional-licence-conditions-exemption-application.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45070107-transfer-of-reg.pdf",
    "https://tfnswforms.transport.nsw.gov.au/45070190-unregistered-vehicle-permit.pdf",
    "https://www.myetoll.transport.nsw.gov.au/sites/default/files/2020-10/E-Toll_form_Application.pdf",
    # other nsw.gov.au / federal AU -- statutory declaration forms
    "https://dcj.nsw.gov.au/documents/legal-and-justice/justice-of-the-peace/nsw-statutory-declaration-forms/nsw-stat-dec-schedule-8.pdf",
    "https://dcj.nsw.gov.au/documents/legal-and-justice/justice-of-the-peace/nsw-statutory-declaration-forms/nsw-stat-dec-schedule-9.pdf",
    "https://www.nsw.gov.au/sites/default/files/2021-10/statutory-declaration-vessel-registration.pdf",
    "https://www.aec.gov.au/Enrolling_to_vote/pdf/forms/statutory-declaration.pdf",
    "https://www.health.nsw.gov.au/Hospitals/privatehealth/Documents/stat-dec.pdf",
    "https://www.nsw.gov.au/sites/default/files/2023-02/revenue-nsw-statutory-declaration-form-individuals.pdf",
    "https://www.nsw.gov.au/sites/default/files/2023-02/revenue-nsw-statutory-declaration-organisations.pdf",
    # workandincome.govt.nz -- New Zealand (Ministry of Social Development benefit forms)
    "https://www.workandincome.govt.nz/assets/documents/forms/accommodation-supplement-for-existing-clients.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/adverse-event-payments-to-hosts-for-accommodation.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/appointment-of-an-agent.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/australia-automatic-pay.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/away-from-home-allowance-application-form.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/board-and-rent-information-form.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/Budget-plan-step-1.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/Budget-plan-step-2.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/Budget-plan-step-3.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/change-of-address-accommodation-costs.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/change-of-bank-account-form.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/change-of-living-situation-for-seniors-form.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/child-disability-allowance-application.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/child-disability-allowance-medical-certificate.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/child-inclusion-form.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/child-support-costs-information-form.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/childcare-and-oscar-subsidy-application.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/childcare-oscar-subsidy-change-of-circumstances.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/childcare-subsidy-verification-form.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/civil-defence-payments-to-evacuees-application-form.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/civilian-amputee-assistance-application.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/community-costs-payment-application.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/community-services-card-application.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/course-participation-assistance-application.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/debt-repayments-authority-form.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/dental-treatment-information.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/disability-allowance-application-for-existing-clients.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/disability-allowance-medical-alarm-assessment-form.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/disability-certificate.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/disability-allowance-review.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/disability-allowance-special-food-information-form.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/disability-allowance-couselling.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/early-learning-payment-application-form.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/employment-earnings-verification.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/confirmation-of-earnings.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/extra-help-application.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/extraordinary-care-application.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/failed-pre-employment-drug-test-result-confirmation-form.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/flexible-childcare-assistance-application-form.pdf",
    "https://www.workandincome.govt.nz/assets/documents/forms/funeral-grant-application.pdf",
    # gov.uk -- DWP benefit claim forms
    "https://assets.publishing.service.gov.uk/media/69ca56dd76f83be521bb3ced/ds700-state-pension-claim-form.pdf",

    # --- round 2 expansion (targeting hosts with proven high usable-yield) --
    # www2.gov.bc.ca -- full BC Residential Tenancy Branch forms-by-number list
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb1.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb5.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb6.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb7.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb8.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb9.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb11a.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12ldr.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12lexh.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12lo.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12lpt.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb12to.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb17.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb18.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb19.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb21.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb22.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb24.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb25.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb26.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb27.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb28.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb29.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb30.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb31.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb32q.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb33.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb33s.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb34.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb35.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb36.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb37.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb38.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb40.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb41.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb42l.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb42o.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb42t.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb43.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/rtb44.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb45.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb46.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb47.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb49.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb50.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb53-p1.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb53-p1d.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb53-p2.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb53-p2d.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb-53-p3.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb-53-p3d.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb54.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb55.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb56.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb56a.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb56b.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb57.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb58.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb59.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb60.pdf",
    "https://www2.gov.bc.ca/assets/gov/housing-and-tenancy/residential-tenancies/forms/rtb61.pdf",
    # dshs.wa.gov -- Washington State DSHS forms (broader than the DCYF subset)
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-001.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-001ca.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-001ch.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-001ko.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-001la.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-001ru.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-001sm.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-001sp.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-001vi.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/17-063.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/09-653.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-057.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-113.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-012.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-078.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/02-528.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-467.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-467ca.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-467ch.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-467ko.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-467la.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-467ru.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-467sm.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-467sp.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-467vi.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/11-154ca.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/11-154ch.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/11-154ko.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/11-154la.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/11-154ru.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/11-154sm.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/11-154sp.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/11-154vi.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/05-013.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-252.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-438.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-050.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-224.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-223.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/18-334.pdf",
    "https://www.dshs.wa.gov/sites/default/files/publications/documents/22-310.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/14-076.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/03-387a.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/10-585.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/27-179.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/10-417.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/15-449.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/10-508.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/13-645.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/06-168.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/06-169.pdf",
    "https://www.dshs.wa.gov/sites/default/files/forms/pdf/21-065.pdf",
    # tribunalsontario.ca -- Ontario Landlord and Tenant Board forms
    "https://tribunalsontario.ca/documents/ltb/Notices%20of%20Rent%20Increase%20%26%20Instructions/N1.pdf",
    "https://tribunalsontario.ca/documents/ltb/Notices%20of%20Rent%20Increase%20%26%20Instructions/N2.pdf",
    "https://tribunalsontario.ca/documents/ltb/Notices%20of%20Rent%20Increase%20%26%20Instructions/N3.pdf",
    "https://tribunalsontario.ca/documents/ltb/Notices%20of%20Rent%20Increase%20%26%20Instructions/N10.pdf",
    "https://tribunalsontario.ca/documents/ltb/Notices%20of%20Termination%20%26%20Instructions/N4.pdf",
    "https://tribunalsontario.ca/documents/ltb/Notices%20of%20Termination%20%26%20Instructions/N5.pdf",
    "https://tribunalsontario.ca/documents/ltb/Notices%20of%20Termination%20%26%20Instructions/N6.pdf",
    "https://tribunalsontario.ca/documents/ltb/Notices%20of%20Termination%20%26%20Instructions/N7.pdf",
    "https://tribunalsontario.ca/documents/ltb/Notices%20of%20Termination%20%26%20Instructions/N8.pdf",
    "https://tribunalsontario.ca/documents/ltb/Other%20Forms/N11.pdf",
    "https://tribunalsontario.ca/documents/ltb/Notices%20of%20Termination%20%26%20Instructions/N12.pdf",
    "https://tribunalsontario.ca/documents/ltb/Notices%20of%20Termination%20%26%20Instructions/N13.pdf",
    "https://tribunalsontario.ca/documents/ltb/Other%20Forms/N14%20-%20Landlord%27s%20Notice%20to%20the%20Spouse%20of%20the%20Tenant%20who%20Vacated%20the%20Rental%20Unit.pdf",
    "https://tribunalsontario.ca/documents/ltb/Landlord%20Applications%20%26%20Instructions/L1.pdf",
    "https://tribunalsontario.ca/documents/ltb/Landlord%20Applications%20%26%20Instructions/L2.pdf",
    "https://tribunalsontario.ca/documents/ltb/Landlord%20Applications%20%26%20Instructions/L3.pdf",
    "https://tribunalsontario.ca/documents/ltb/Landlord%20Applications%20%26%20Instructions/L4.pdf",
    "https://tribunalsontario.ca/documents/ltb/Landlord%20Applications%20%26%20Instructions/L5.pdf",
    "https://tribunalsontario.ca/documents/ltb/Landlord%20Applications%20%26%20Instructions/L6.pdf",
    "https://tribunalsontario.ca/documents/ltb/Landlord%20Applications%20%26%20Instructions/L7.pdf",
    "https://tribunalsontario.ca/documents/ltb/Landlord%20Applications%20%26%20Instructions/L8.pdf",
    "https://tribunalsontario.ca/documents/ltb/Landlord%20Applications%20%26%20Instructions/L9.pdf",
    "https://tribunalsontario.ca/documents/ltb/Landlord%20Applications%20%26%20Instructions/L10.pdf",
    "https://tribunalsontario.ca/documents/ltb/Landlord%20Applications%20%26%20Instructions/A1.pdf",
    "https://tribunalsontario.ca/documents/ltb/Landlord%20Applications%20%26%20Instructions/A2.pdf",
    "https://tribunalsontario.ca/documents/ltb/Landlord%20Applications%20%26%20Instructions/A4.pdf",
    "https://tribunalsontario.ca/documents/TO/TO001E.pdf",
    "https://tribunalsontario.ca/documents/ltb/Other%20Forms/Additional_Residential_Addresses_Form.pdf",
    "https://tribunalsontario.ca/documents/ltb/Other%20Forms/Affidavit.pdf",
    # planning.lacounty.gov -- Los Angeles County planning/permit forms
    "https://planning.lacounty.gov/wp-content/uploads/2025/02/Rebuild-Online-Supplemental-Form.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2025/01/base_application_in-person_disaster-recovery.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2025/09/Disaster-Recovery-Permit_Procedure-A_Modification-Findings_09232025_fillable.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2025/06/Permit-Fee-Waiver-Refund-Form_fillable.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2025/01/Temporary-Housing-InPersonApplication.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2023/05/New-ACC_License_Referral_Form_Fillable.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2022/09/dmv_application.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2022/10/rebuild-letter_application.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2022/10/zoning-verification_application.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2022/10/in-person-base-application.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2023/02/zp_pre-app_application.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2023/02/smmlcp_pre-app_application.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2022/10/sea_counseling_application-checklist.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2022/10/sea_counseling_application.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2022/09/Owner_Acknowledge_form.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2022/09/landuse_checklist.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2026/06/ROLD-Supplemental-Application_Final.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2022/09/env-assess-info_form.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2022/09/pre-existing_site_conditions_and_occupant_income_certification.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2022/09/zoning-permit-checklist.pdf",
    "https://planning.lacounty.gov/wp-content/uploads/2022/09/ownership-and-consent_affidavit.pdf",
    # pa.gov -- Pennsylvania PennDOT forms
    "https://www.pa.gov/content/dam/copapwp-pagov/en/penndot/documents/public/dvspubsforms/bdl/bdl-form/dl-135.pdf",
    "https://www.pa.gov/content/dam/copapwp-pagov/en/penndot/documents/public/dvspubsforms/bdl/bdl-form/dl-503.pdf",
    "https://www.pa.gov/content/dam/copapwp-pagov/en/penndot/documents/public/dvspubsforms/bmv/bmv-forms/mv-140.pdf",
]


class FetchError(Exception):
    """Raised by _download on any request failure. status is the HTTP code
    when the server responded with one, else None (timeout/connection error)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# classify() -- no network, never raises
# --------------------------------------------------------------------------

class _ClassifyTimeout(Exception):
    pass


def _alarm_handler(signum, frame):
    raise _ClassifyTimeout()


def classify(pdf_path) -> dict:
    """Classify a local PDF file. Pure function of the file's bytes -- no
    network. Never raises: any failure comes back as verdict "unusable" with
    a human-readable "reason"."""
    pdf_path = Path(pdf_path)
    record = _blank_record(pdf_path)

    have_alarm = hasattr(signal, "SIGALRM")
    old_handler = None
    if have_alarm:
        try:
            old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
            signal.alarm(CLASSIFY_TIMEOUT_SECONDS)
        except Exception:
            have_alarm = False  # e.g. not the main thread -- skip the timeout, keep going
    try:
        return _classify_inner(pdf_path, record)
    except _ClassifyTimeout:
        record["verdict"] = "unusable"
        record["reason"] = f"classification exceeded {CLASSIFY_TIMEOUT_SECONDS}s"
        return record
    except Exception as e:  # belt and suspenders -- classify() must never raise
        record["verdict"] = "unusable"
        record["reason"] = f"unexpected error: {e!r}"
        return record
    finally:
        if have_alarm:
            try:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
            except Exception:
                pass


def _blank_record(pdf_path):
    return {
        "file": pdf_path.name,
        "sha256": None,
        "bytes": None,
        "pages": None,
        "producer": None,
        "creator": None,
        "has_acroform": False,
        "widget_count": 0,
        "thin_h_rects": 0,
        "thin_v_rects": 0,
        "checkbox_glyphs": 0,
        "underscore_chars": 0,
        "fonts": [],
        "verdict": "unusable",
        "reason": None,
    }


def _classify_inner(pdf_path, record):
    try:
        data = pdf_path.read_bytes()
    except Exception as e:
        record["reason"] = f"cannot read file: {e}"
        return record

    record["bytes"] = len(data)
    record["sha256"] = hashlib.sha256(data).hexdigest()

    if record["bytes"] == 0:
        record["reason"] = "empty file"
        return record
    if record["bytes"] > MAX_FILE_BYTES:
        record["reason"] = f"exceeds {MAX_FILE_BYTES} byte limit"
        return record

    # ---- structural pass: encryption, page count, AcroForm, metadata ------
    try:
        reader = pypdf.PdfReader(str(pdf_path))
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception:
                ok = 0
            if not ok:
                record["reason"] = "encrypted, no usable password"
                return record

        pages = len(reader.pages)
        record["pages"] = pages
        if pages == 0:
            record["reason"] = "zero pages"
            return record
        if pages > MAX_PAGES:
            record["reason"] = f"exceeds {MAX_PAGES} page limit ({pages} pages)"
            return record

        meta = reader.metadata or {}
        record["producer"] = str(meta.get("/Producer") or "")
        record["creator"] = str(meta.get("/Creator") or "")

        root = reader.trailer.get("/Root") or {}
        acroform = root.get("/AcroForm")
        record["has_acroform"] = bool(acroform)
        if acroform:
            try:
                fields = reader.get_fields()
            except Exception:
                fields = None
            record["widget_count"] = len(fields) if fields else 0
    except Exception as e:
        record["reason"] = f"malformed (pypdf): {e}"
        return record

    # ---- vector/text pass: rects, glyphs, underscores, fonts --------------
    try:
        vrects = hrects = glyphs = underscores = total_chars = 0
        font_counter = Counter()
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                rects = page.rects
                vrects += len([r for r in rects if r["width"] < 3 and r["height"] >= 5])
                hrects += len([r for r in rects if r["height"] < 3 and r["width"] >= 5])
                chars = page.chars
                total_chars += len(chars)
                for c in chars:
                    if c["text"] in CHECK_GLYPHS:
                        glyphs += 1
                    elif c["text"] == "_":
                        underscores += 1
                    fname = c.get("fontname")
                    if fname:
                        font_counter[fname] += 1
        record["thin_v_rects"] = vrects
        record["thin_h_rects"] = hrects
        record["checkbox_glyphs"] = glyphs
        record["underscore_chars"] = underscores
        record["fonts"] = [f for f, _ in font_counter.most_common(5)]
    except Exception as e:
        record["reason"] = f"malformed (pdfplumber): {e}"
        return record

    avg_chars_per_page = total_chars / record["pages"]
    if avg_chars_per_page < 50:
        record["verdict"] = "scan"
        record["reason"] = f"avg {avg_chars_per_page:.1f} chars/page, no real text layer"
        return record

    if record["has_acroform"]:
        signature = f"{record['producer']} {record['creator']}".lower()
        if "designer" in signature or "livecycle" in signature:
            record["verdict"] = "fillable-livecycle"
        else:
            record["verdict"] = "fillable-other"
        record["reason"] = None
        return record

    if record["thin_h_rects"] > 20 and record["thin_v_rects"] > 20:
        record["verdict"] = "flat-wordlike"
    else:
        record["verdict"] = "flat-sparse"
    record["reason"] = None
    return record


# --------------------------------------------------------------------------
# fetch() -- the polite network side
# --------------------------------------------------------------------------

def _fetch_robots_txt(robots_url):
    """Fetch robots.txt as text. Separated out so tests can monkeypatch this
    one function instead of hitting the network."""
    req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def _robots_allowed(robots_cache, url):
    """True if USER_AGENT may fetch url. Caches one RobotFileParser per host.
    An unreachable/missing robots.txt is treated as allow-all, per convention."""
    parsed = urlparse(url)
    host = parsed.netloc
    if host not in robots_cache:
        robots_url = f"{parsed.scheme}://{host}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        try:
            text = _fetch_robots_txt(robots_url)
            rp.parse(text.splitlines())
        except Exception:
            rp = None
        robots_cache[host] = rp
    rp = robots_cache[host]
    if rp is None:
        return True
    return rp.can_fetch(USER_AGENT, url)


def _throttle(last_times, host):
    """Block until at least MIN_HOST_DELAY seconds have passed since the last
    request to this host."""
    now = time.monotonic()
    wait = MIN_HOST_DELAY - (now - last_times.get(host, -1e9))
    if wait > 0:
        time.sleep(wait)
    last_times[host] = time.monotonic()


def _download(url):
    """One HTTP GET, timed out, capped at MAX_FILE_BYTES. Raises FetchError
    on any failure. Separated out so tests can monkeypatch the network."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = resp.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise FetchError(f"exceeds {MAX_FILE_BYTES} byte cap")
            return data
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}", status=e.code) from e
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        raise FetchError(str(e)) from e


def _download_with_retries(url):
    """At most MAX_RETRIES retries, with backoff, for transient failures only.
    A 403/429 is never retried -- it is the caller's job to block the host."""
    last_exc = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _download(url)
        except FetchError as e:
            last_exc = e
            if e.status in (403, 429):
                raise
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            raise
    raise last_exc  # pragma: no cover -- loop always returns or raises


def _load_manifest(manifest_path):
    if not manifest_path.exists():
        return {"records": [], "skipped": [], "blocked_hosts": []}
    try:
        data = json.loads(manifest_path.read_text())
    except Exception:
        return {"records": [], "skipped": [], "blocked_hosts": []}
    data.setdefault("records", [])
    data.setdefault("skipped", [])
    data.setdefault("blocked_hosts", [])
    return data


def fetch(urls, out_dir, limit=MAX_FILES) -> dict:
    """Fetch and classify every URL not already cached, up to `limit` new
    files this run. Returns the manifest dict (also written to
    <out_dir>/manifest.json). Never fetches a URL or content hash it already
    holds from a previous run."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"

    manifest = _load_manifest(manifest_path)
    records = manifest["records"]
    skipped = manifest["skipped"] = []          # this run's skip log only
    blocked_hosts = set(manifest["blocked_hosts"])

    known_urls = {r["url"] for r in records if r.get("url")}
    known_hashes = {r["sha256"] for r in records if r.get("sha256")}

    robots_cache = {}
    last_times = {}
    fetched_this_run = 0

    for url in urls:
        if fetched_this_run >= limit:
            skipped.append({"url": url, "reason": "run limit reached"})
            continue
        if url in known_urls:
            continue  # already held -- never re-fetch

        host = urlparse(url).netloc
        if host in blocked_hosts:
            skipped.append({"url": url, "reason": "host blocked earlier this run"})
            continue

        try:
            if not _robots_allowed(robots_cache, url):
                skipped.append({"url": url, "reason": "disallowed by robots.txt"})
                continue
        except Exception as e:
            skipped.append({"url": url, "reason": f"robots.txt check failed: {e}"})
            continue

        _throttle(last_times, host)
        try:
            data = _download_with_retries(url)
        except FetchError as e:
            if e.status in (403, 429):
                blocked_hosts.add(host)
                skipped.append({"url": url, "reason": f"host blocked after HTTP {e.status}"})
            else:
                skipped.append({"url": url, "reason": f"fetch failed: {e}"})
            continue
        except Exception as e:
            skipped.append({"url": url, "reason": f"fetch failed: {e}"})
            continue

        digest = hashlib.sha256(data).hexdigest()
        if digest in known_hashes:
            skipped.append({"url": url, "reason": "duplicate content already held"})
            continue

        local_path = out_dir / f"{digest[:16]}.pdf"
        local_path.write_bytes(data)

        record = classify(local_path)
        record["url"] = url
        record["file"] = local_path.name
        records.append(record)
        known_urls.add(url)
        known_hashes.add(digest)
        fetched_this_run += 1

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
        "skipped": skipped,
        "blocked_hosts": sorted(blocked_hosts),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    _write_readme(out_dir, manifest)
    return manifest


def _write_readme(out_dir, manifest):
    records = manifest["records"]
    counts = Counter(r["verdict"] for r in records)
    lines = [
        "# Real-world government form corpus",
        "",
        f"Generated {manifest['generated_at']}. {len(records)} files held total.",
        "",
        "## Verdict counts",
        "",
    ]
    for v in VERDICTS:
        lines.append(f"- {v}: {counts.get(v, 0)}")
    lines.append("")

    flat = [r for r in records if r["verdict"] == "flat-wordlike"]
    lines.append(f"## flat-wordlike candidates ({len(flat)})")
    lines.append("")
    lines.append(
        "These are structurally like fixtures/safer.pdf (thin-rect table "
        "borders, Webdings/Wingdings checkbox glyphs, underscore write-on "
        "lines, no AcroForm) and are candidates for hand-labelled ground truth."
    )
    lines.append("")
    if not flat:
        lines.append("None found.")
    for r in flat:
        lines.append(f"- {r.get('url', r['file'])}")
        lines.append(
            f"  - file: {r['file']}, pages: {r['pages']}, producer: {r['producer']!r}"
        )
        lines.append(
            f"  - thin_h_rects={r['thin_h_rects']} thin_v_rects={r['thin_v_rects']} "
            f"checkbox_glyphs={r['checkbox_glyphs']} underscore_chars={r['underscore_chars']}"
        )
    lines.append("")

    others = [r for r in records if r["verdict"] != "flat-wordlike"]
    if others:
        lines.append(f"## Other verdicts ({len(others)})")
        lines.append("")
        for r in others:
            lines.append(
                f"- {r['verdict']}: {r.get('url', r['file'])}"
                + (f" ({r['reason']})" if r.get("reason") else "")
            )
        lines.append("")

    if manifest["skipped"]:
        lines.append(f"## Skipped this run ({len(manifest['skipped'])})")
        lines.append("")
        for s in manifest["skipped"]:
            lines.append(f"- {s['url']}: {s['reason']}")
        lines.append("")

    if manifest["blocked_hosts"]:
        lines.append("## Hosts blocked this run (403/429)")
        lines.append("")
        for h in manifest["blocked_hosts"]:
            lines.append(f"- {h}")
        lines.append("")

    (out_dir / "README.md").write_text("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="eval/corpus/real", help="output directory")
    parser.add_argument("--limit", type=int, default=MAX_FILES, help="max new files this run")
    parser.add_argument(
        "--urls", nargs="*", default=None,
        help="override the built-in seed URL list (mainly for testing)",
    )
    args = parser.parse_args(argv)
    urls = args.urls if args.urls is not None else SEED_URLS
    manifest = fetch(urls, args.out, limit=args.limit)
    counts = Counter(r["verdict"] for r in manifest["records"])
    print(f"fetched {len(manifest['records'])} files this run "
          f"({len(manifest['skipped'])} skipped)")
    for v in VERDICTS:
        print(f"  {v}: {counts.get(v, 0)}")


if __name__ == "__main__":
    main()
