"""Flow K legal copy: Terms & Privacy summaries and full texts.

Versions are bumped when the copy materially changes so we can record what each
user accepted (Terms) or was shown (Privacy). Support/contact details are
intentionally omitted — they go through the in-service support flow, not email.
"""

TERMS_VERSION = "2026-06"
PRIVACY_VERSION = "2026-06"

TERMS_SUMMARY = (
    "Terms summary:\n\n"
    "This service helps you organise delivery-work records such as mileage, "
    "earnings and courier-related expenses.\n\n"
    "It does not file your tax return, act as your accountant, give formal tax "
    "advice or calculate your final tax liability.\n\n"
    "You're responsible for checking your records before using them or sharing "
    "them with an accountant.\n\n"
    "Reply FULL TERMS to read the full Terms."
)

PRIVACY_SUMMARY = (
    "Privacy summary:\n\n"
    "We use your WhatsApp messages and confirmed records to provide the service. "
    "This may include mileage, earnings, courier expenses, settings, support "
    "messages and payment/access status if payment is introduced.\n\n"
    "Earnings screenshots are used for extraction and are not saved.\n\n"
    "Reply FULL PRIVACY to read the full Privacy Notice."
)

TERMS_FULL = (
    "Terms and Conditions\n\n"
    "This service helps food delivery couriers organise delivery-work records, "
    "including mileage, earnings and courier-related expenses. It is a "
    "record-keeping assistant only.\n\n"
    "It does NOT:\n"
    "• file your tax return\n"
    "• act as your accountant or tax adviser\n"
    "• act as an HMRC agent\n"
    "• calculate your final tax liability\n"
    "• guarantee any expense or deduction will be accepted by HMRC\n"
    "• cover your full Self Assessment return\n\n"
    "You remain responsible for checking your records and for your tax "
    "submissions.\n\n"
    "Vehicle costs use the simplified-mileage method: you track delivery-business "
    "miles instead of submitting petrol, insurance, repair, servicing, MOT, road "
    "tax or similar receipts. Actual vehicle-cost calculations aren't currently "
    "supported.\n\n"
    "You're responsible for the accuracy of what you send. Before a record is "
    "added you may be asked to confirm, edit or delete it.\n\n"
    "Automated extraction may be used to read earnings screenshots or text. It can "
    "be incorrect, so you must check and confirm extracted information before it "
    "becomes a confirmed record.\n\n"
    "Record exports are provided for review — they are not completed tax returns.\n\n"
    "If payment is introduced, pricing and availability will be shown before "
    "payment. Payments may be handled by a third party such as Stripe; we don't "
    "store card details directly.\n\n"
    "The service may change over time. Nothing in these Terms limits liability "
    "where it would be unlawful to do so. These Terms are governed by the laws of "
    "England and Wales."
)

PRIVACY_FULL = (
    "Privacy Notice\n\n"
    "We use personal data to provide the courier record assistant. This may "
    "include:\n"
    "• your WhatsApp number and account/access status\n"
    "• messages you send and support messages\n"
    "• mileage, vehicle type and settings\n"
    "• tax estimate level you select\n"
    "• platform earnings and courier expense records\n"
    "• confirm/edit/delete history\n"
    "• payment/access status if payment is introduced\n"
    "• technical and security logs\n\n"
    "We do NOT intentionally collect: UTR, National Insurance number, home "
    "address, bank login or card details, full tax-return information, "
    "PAYE/P60/P45, or pension/benefits/rental/investment income.\n\n"
    "Earnings screenshots are processed temporarily to extract earnings; by "
    "default the confirmed record is saved, not the raw screenshot. Receipt photos "
    "aren't automatically read by default.\n\n"
    "We may share data with providers needed to operate the service (e.g. "
    "WhatsApp/Meta, cloud hosting, file storage, AI/OCR providers, Stripe if "
    "payment is introduced, logging/security providers, professional advisers). We "
    "do not sell your personal data.\n\n"
    "You may request to access, export, correct or delete your data, subject to "
    "legal, security, accounting or technical retention requirements. We keep data "
    "only as long as reasonably needed.\n\n"
    "Marketing messages are separate and optional — accepting the Terms is not "
    "marketing consent. You can raise concerns through the support flow or the UK "
    "ICO (https://ico.org.uk)."
)
