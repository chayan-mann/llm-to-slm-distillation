# Ticket Router Spec (v1.3)

Source of truth for the teacher prompt (Phase 1), labeling (Phase 3), quality review (Phase 4), formatting (Phase 5) and evaluation (Phase 7). If the spec and anything downstream disagree, the spec wins; fix the downstream artifact or bump the spec version.

**Domain:** customer support for a B2B/B2C SaaS product (web app + mobile apps + public API).

---

## 1. Task

Given the text of one support ticket, output a single JSON object that:

1. assigns exactly **one** category out of 30 (the *primary intent*),
2. flags whether the ticket contains more than one intent,
3. extracts 3 fields: `urgency`, `account_identifier`, `reference_id`.

No system prompt at inference. The student sees only the ticket text and must emit only the JSON.

## 2. Input format

```
Subject: <subject line, if any>

<ticket body>
```

- If there is no subject, omit the `Subject:` line and the blank line after it.
- The body is passed as is: no cleanup of typos, signatures or quoted email threads.
- Max input length: 4,000 characters. Longer tickets are truncated to the first 4,000 characters, never dropped. The same truncated text is used for teacher labeling, training and inference, so the teacher never sees text the student can't.
- Language: English only for v1.

## 3. Output format

One line of compact JSON, keys in this exact order, with no other text, markdown fences or explanation:

```json
{"category":"login_issue","multi_intent":false,"urgency":"high","account_identifier":"jane@acme.io","reference_id":null}
```

### JSON Schema

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["category", "multi_intent", "urgency", "account_identifier", "reference_id"],
  "properties": {
    "category":           { "type": "string", "enum": ["<the 30 ids in §4>"] },
    "multi_intent":       { "type": "boolean" },
    "urgency":            { "type": "string", "enum": ["low", "normal", "high", "critical"] },
    "account_identifier": { "type": ["string", "null"] },
    "reference_id":       { "type": ["string", "null"] }
  }
}
```

Rules:
- All 5 keys are **always present**. A field that isn't mentioned is `null`, never left out and never `""`.
- String values for `account_identifier` and `reference_id` are copied **verbatim** from the ticket: same casing, no added or removed prefixes.

---

## 4. Categories (30)

Each category has a definition, examples of what it covers, and the nearest neighbours it must **not** be confused with. The boundary notes matter most.

### Account & access (6)

| id | Definition | Includes | Not this → use instead |
|---|---|---|---|
| `login_issue` | The user can't sign in with their normal credentials. | Wrong password, reset email not arriving, reset link expired, "account locked" | 2FA codes failing → `mfa_issue`; SSO/SAML login errors → `sso_setup`; login from an unknown location → `compromised_account` |
| `mfa_issue` | Problems with two-factor or multi-factor authentication. | Lost phone, authenticator codes rejected, backup codes, turning 2FA on/off | Other password problems → `login_issue` |
| `sso_setup` | Configuring or fixing enterprise single sign-on and provisioning. | SAML/OIDC setup, IdP errors (Okta, Azure AD), SCIM provisioning, domain verification | Individual users forgetting passwords → `login_issue` |
| `account_settings` | Changing the user's own profile or preferences. | Change email or name, avatar, time zone, language, notification preferences, connected third-party apps | Other people's access → `user_management`; deleting the account → `account_closure` |
| `user_management` | Managing *other* users in a workspace. | Inviting or removing teammates, roles and permissions, transferring ownership, invites not arriving | Buying more seats → `plan_change` |
| `account_closure` | Permanently deleting an account or workspace. | "Delete my account", "close our workspace" | Just stopping payment → `cancellation`; deletion requested under GDPR/CCPA → `privacy_compliance` |

### Billing (7)

| id | Definition | Includes | Not this → use instead |
|---|---|---|---|
| `unexpected_charge` | The customer says a charge is **wrong**. | Charged twice, charged after cancelling, wrong amount, unfamiliar charge | Charge was correct but they want money back → `refund_request` |
| `refund_request` | Asking for money back for a charge they agree was valid. | "Forgot to cancel, can I get a refund?", unhappy with the product, asking about the refund policy | Disputing the charge itself → `unexpected_charge` |
| `plan_change` | Changing an existing paid subscription, or an existing customer asking how to. | Upgrade or downgrade, add/remove seats, add-ons, switch monthly/annual, "How do we upgrade?" | Stopping entirely → `cancellation`; only asking what's available or what it costs, with no change requested (even from a customer) → `sales_inquiry` |
| `cancellation` | Stopping a subscription or turning off auto-renewal. | "Cancel my plan", "don't renew", "how do I unsubscribe from the paid plan" | Deleting data/account → `account_closure`; unsubscribing from marketing email → `account_settings` |
| `payment_failure` | A payment isn't going through, or the payment method needs changing. | Card declined, updating the card, failed renewal, account suspended or downgraded for non-payment (including restoring the plan afterwards), switching to bank transfer / direct debit / invoice terms | Charge went through but is wrong → `unexpected_charge` |
| `invoice_request` | Billing documents and billing details. | Invoice/receipt copies, VAT/tax ID, billing address, PO numbers, W-9 | Disputing the amount → `unexpected_charge` |
| `trial_extension` | About the free trial. | Extending the trial, trial ended early, what happens when the trial ends | Choosing a paid plan → `sales_inquiry` |

### Technical (9)

| id | Definition | Includes | Not this → use instead |
|---|---|---|---|
| `bug_report` | A specific feature behaves wrongly for this user; the service is otherwise up. | Button does nothing, wrong calculation, UI glitch, error on one action | Whole service down → `service_outage`; slow but correct → `performance_issue`; mobile only → `mobile_app_issue` |
| `service_outage` | The product (or a major part of it) is unreachable or failing for everyone or for all of the user's work. | "Site is down", 5xx on every page, "nobody on our team can load anything", a whole major feature failing for everyone ("nobody can open any attachment") | Broken only for specific items or users → `bug_report` |
| `performance_issue` | Things work but are slow. | Slow page loads, timeouts under load, lag | Totally unreachable → `service_outage` |
| `data_sync_issue` | Data is missing, lost, duplicated or out of sync *inside the product*. | Records disappeared, changes not saving, devices showing different data | Third-party sync problems → `integration_issue`; file import/export → `import_export` |
| `integration_issue` | Problems with a built-in third-party integration. | Slack/Salesforce/Zapier/Google connection failing, OAuth reconnect, data not flowing to a partner app | The customer's own code calling our API → `api_support` |
| `api_support` | Developers using the public API or webhooks. | Auth tokens, rate limits, endpoint errors, webhook delivery, SDK questions | Built-in integrations → `integration_issue` |
| `import_export` | Moving data in or out via files or migration tools. | CSV/Excel import failing, export requests, migrating from a competitor | Data vanishing inside the product → `data_sync_issue` |
| `mobile_app_issue` | Problems that only happen in the iOS/Android app. | App crashes, push notifications, app login loops, app store/version issues | Same problem on web too → the matching category (e.g. `bug_report`) |
| `email_delivery` | Emails *sent by the product* not arriving or looking wrong. | Notification emails missing, going to spam, broken templates | Password-reset email specifically → `login_issue`; invite emails → `user_management` |

### Product usage (3)

| id | Definition | Includes | Not this → use instead |
|---|---|---|---|
| `how_to_question` | Asking how to do something, or whether something is possible, in general product usage. | "How do I…", "Where is the setting for…", "Is there a way to…", "Can I…", onboarding questions | Customer says it's missing or asks for it to be added → `feature_request`; it exists but is broken → `bug_report`; question is about an area with its own category → that category (§5) |
| `feature_request` | The customer asks for something to be added or changed, or says a capability is missing or impossible. | "Please add…", "It would be great if…", "Do you plan to support…", "It only offers X, I need Y" | A plain "Is there a way to…?" / "Can I…?" without saying it's missing → `how_to_question` or the area's category (§5) |
| `product_feedback` | General opinions with no specific request. | Praise, general complaints about the product or support experience, survey-style comments | Specific broken behaviour → `bug_report`; wants money back → `refund_request` |

### Security & compliance (3)

| id | Definition | Includes | Not this → use instead |
|---|---|---|---|
| `security_report` | Reporting a vulnerability in the product. | Responsible disclosure, "I found an XSS", bug bounty questions | Their own account being attacked → `compromised_account` |
| `compromised_account` | Suspected unauthorized access to the customer's account. | Unknown logins, settings changed by someone else, "I think I was hacked", phishing emails that look like they came from us | Just can't log in → `login_issue` |
| `privacy_compliance` | Legal/compliance requests about data. | GDPR/CCPA access or erasure, DPA, SOC 2/ISO reports, data residency, security questionnaires | Plain "delete my account" with no legal framing → `account_closure` |

### Other (2)

| id | Definition | Includes | Not this → use instead |
|---|---|---|---|
| `sales_inquiry` | Buying questions from prospects or customers. | Pricing, plan comparison, enterprise quotes, demos, discounts (nonprofit/edu), partnerships/reselling | Changing an existing plan → `plan_change` |
| `out_of_scope` | Not a support request we can act on. | Spam, marketing pitches, empty/gibberish text, messages meant for another company, auto-replies ("out of office"), automated bounce notices, legal/trademark complaints about the company | |

---

## 5. Multi-intent policy

- Output exactly one `category`: the **primary intent**.
- Set `multi_intent: true` if the ticket contains **two or more intents that would map to different categories** and each would need its own action. Otherwise `false`.
- Background context doesn't count as an intent ("I've been a customer for 5 years and love it, but my card was declined" → `payment_failure`, `multi_intent: false`).
- Several asks within the same category are not multi-intent, including asking for the fix, compensation and explanation of the same problem ("remove this add-on and refund it" is one `unexpected_charge` intent). `multi_intent: true` only when another request would, on its own, go to a different category.

**Choosing the primary intent**, in order:
1. **Severity override:** if any intent is `compromised_account`, `security_report` or `service_outage`, that intent is primary (in that order).
2. Otherwise, the intent that **blocks the customer from using the product** right now.
3. Otherwise, the intent the customer **states first** or clearly emphasizes (e.g. in the subject line).

Example: "I was double charged this month, and also, can you add dark mode?" → `unexpected_charge`, `multi_intent: true`.

**How-to questions about a specific area:** if a "how do I…" question is about an area that has its own category, use that category, not `how_to_question`. For example, "How do I invite a teammate?" → `user_management` and "How do I cancel?" → `cancellation`. `how_to_question` is for general product usage that no other category covers.

**"Is it possible…?" questions:** the ticket usually can't tell us whether a capability exists, so don't guess. Use `feature_request` only when the customer asks for something to be added or changed, or says it's missing or impossible ("it only offers daily and weekly", "please add", "if not, please consider it"). A plain "Is there a way to…?" / "Can I…?" / "Are there…?" question goes to the area's category (e.g. "Can admins prevent members from exporting?" → `user_management`, "Can we schedule nightly exports?" → `import_export`), or to `how_to_question` if no area category fits.

---

## 6. Extracted fields

### 6.1 `urgency` — enum, always set

Based on **impact described in the ticket**, not on tone. An angry ticket about a typo is still `low`/`normal`.

| value | Use when |
|---|---|
| `critical` | Security incident, vulnerability report or compromised account; outage affecting a whole team/org; confirmed loss of shared/team data, or data loss that is still happening; customer says production/revenue is down right now. |
| `high` | One user fully blocked from core work (can't log in, account suspended); one person lost their own work (not shared data, not ongoing); payment failure that will suspend service; explicit deadline within ~24h. |
| `normal` | Default. Something is broken or needed, but there's a workaround or no time pressure. |
| `low` | Feature requests, feedback, how-to questions with no blocker, pricing questions, cosmetic bugs with no functional impact, `out_of_scope`. |

Words like "URGENT!!!" in the subject can move `normal` → `high` only if the body backs up a real blocker or deadline.

Urgency reflects what is happening **now**. A resolved incident is never `critical`/`high`: asking for an explanation or incident report → `normal`; only asking whether something happened → `low`. Past problems mentioned as background don't raise today's urgency. The category of a past-incident ticket follows the current ask (credit → `refund_request`; incident report → `service_outage`). Exception: data lost in a past incident that still needs recovering stays urgent.

### 6.2 `account_identifier` — string | null

A string in the ticket that identifies the **customer's own account or workspace**.

- Counts: account/org/workspace IDs (`acct_83jd92`, `Org ID: 44120`), workspace URLs or subdomains (`acme.ourapp.com`), the customer's email address when given as their login.
- Doesn't count: a person's name alone, company name alone, third-party emails, our support address, signature email only when another identifier is present.
- If several are present, pick in this order: **explicit account/workspace ID > workspace URL/subdomain > email**. If there's a tie, take the first one that appears.
- If the customer gives both an old and a new email (e.g. when changing their email), the old one is their current login.
- Copy verbatim. Extract the URL/subdomain as written without adding `https://`.
- Not present → `null`.

### 6.3 `reference_id` — string | null

The most relevant **identifier of a specific object or event** the support agent would look up.

- Counts: invoice numbers, charge/transaction IDs, order/subscription IDs, prior ticket numbers, API request IDs, product-specific error codes (`ERR_SYNC_409`, `E1043`), and error codes from third-party services shown in our product or integrations (`ZX-401` from Zapier, `AADSTS50011` from Azure AD).
- Doesn't count: account identifiers (those go in §6.2), bare protocol status codes alone, whether HTTP ("getting a 500") or SMTP ("554 5.7.1"), dates, amounts, version numbers, URLs to pages.
- If several are present, pick the one **most relevant to the primary category** (e.g. the invoice number for `unexpected_charge`). If that's still unclear, take the first one that appears.
- Copy verbatim, including prefixes like `INV-` or `#` if they're part of the token (`#48213` → `"#48213"`).
- Not present → `null`.

---

## 7. Edge cases

| Situation | Output |
|---|---|
| Empty, whitespace-only or gibberish ticket | `out_of_scope`, `low`, `multi_intent: false`, both fields `null` |
| Auto-reply / out-of-office | `out_of_scope`, `low` |
| Any `out_of_scope` ticket | Both `account_identifier` and `reference_id` are `null`, even if the text contains IDs (e.g. another company's order number). |
| Non-English ticket | Not in the v1 dataset (English only). If one shows up at inference, behaviour is undefined. |
| Long quoted email thread | Classify the **newest** message; use older quoted text only for context and identifiers. |
| Customer asks about a competitor's product | `out_of_scope` unless it's a migration question (`import_export`) or a buying comparison (`sales_inquiry`). |
| Ticket fits no category but is a real support need | Closest category by the boundary rules. `out_of_scope` is only for non-requests, never a "misc" bin. |
| Identifier looks malformed (e.g. typo'd email) | Still extract verbatim; don't correct it. |

---

## 8. Worked examples

**A — single intent, both fields present**
```
Subject: Charged twice for September

Hi, invoice INV-20931 shows two charges of $49 on my card. My workspace is acme.ourapp.com. Please fix.
```
```json
{"category":"unexpected_charge","multi_intent":false,"urgency":"normal","account_identifier":"acme.ourapp.com","reference_id":"INV-20931"}
```

**B — multi-intent with severity override**
```
we cant log in since this morning, the whole team gets error E5002. also pls send me last months invoice
```
```json
{"category":"service_outage","multi_intent":true,"urgency":"critical","account_identifier":null,"reference_id":"E5002"}
```

**C — angry tone, low impact**
```
Subject: URGENT!!!!

Your export button says "Exprot". Embarrassing. Fix it.
```
```json
{"category":"bug_report","multi_intent":false,"urgency":"low","account_identifier":null,"reference_id":null}
```

**D — refund vs dispute**
```
I forgot to cancel before renewal and got charged for the annual plan yesterday. I don't need it anymore — can I get a refund? Login is dan.k@gmail.com
```
```json
{"category":"refund_request","multi_intent":false,"urgency":"normal","account_identifier":"dan.k@gmail.com","reference_id":null}
```

**E — compromised account**
```
Someone logged into my account from Brazil and changed my email. Account ID acct_77fk2. Please lock it NOW.
```
```json
{"category":"compromised_account","multi_intent":false,"urgency":"critical","account_identifier":"acct_77fk2","reference_id":null}
```

**F — out of scope**
```
Hi! We help SaaS companies 10x their SEO. Can I get 15 minutes on your calendar?
```
```json
{"category":"out_of_scope","multi_intent":false,"urgency":"low","account_identifier":null,"reference_id":null}
```

---

## 9. Evaluation implications (for Phase 7)

- `category`: exact-match accuracy + macro-F1 across the 30 classes, plus a confusion matrix focused on the boundary pairs in §4.
- `multi_intent`: precision/recall on `true`.
- `urgency`: exact match + "off by more than one level" rate (confusing `critical` with `low` is much worse than `high` with `critical`).
- `account_identifier`, `reference_id`: exact string match, reported separately for "gold is null" and "gold is non-null" (a model that always says `null` must not look good).
- JSON validity: parses, and matches the schema in §3 exactly (key set, key order, enum values).

---

## 10. Open questions

None.

Resolved:
- **Q4.** `out_of_scope` stays one bucket.
- **Q1.** Fields stay as `urgency`, `account_identifier`, `reference_id`.
- **Q2.** English only for v1.
- **Q3.** Tickets over 4,000 characters are truncated, not dropped.

## Changelog

- v1.3 — From the human-style review of 100 v1.2 labels: same-category follow-ups (fix + refund + explanation) are not multi-intent (§5); SMTP and other protocol status codes aren't `reference_id` (§6.3); downgraded for non-payment → `payment_failure`; connected third-party apps → `account_settings` (§4). Validator now requires extracted IDs to match a whole token.
- v1.2 — From the Phase 4 review of 1,500 teacher labels: "Is it possible…?" questions go to the area category or `how_to_question`, `feature_request` only when something is asked for or said to be missing (§4, §5); urgency reflects the current state, resolved incidents aren't `critical`/`high` (§6.1); existing customers asking how to change plan → `plan_change` (§4); switching to bank transfer/invoice terms → `payment_failure` (§4); a whole major feature down for everyone → `service_outage` (§4); bounce notices and legal/trademark complaints → `out_of_scope` (§4).
- v1.1 — From the first 20 teacher labels: `out_of_scope` tickets always have both identifier fields `null` (§7); third-party error codes count as `reference_id` (§6.3); data loss is `critical` only for shared/team data or ongoing loss, one person's own lost work is `high` (§6.1).
- v1.0 — Resolved Q4 (keep `out_of_scope` as one bucket). Added the how-to rule in §5, vulnerability reports as `critical` in §6.1, and the old-vs-new email rule in §6.2.
- v0.2 — Resolved Q1–Q3: keep the 3 fields, English only, truncate long tickets.
- v0.1 — initial draft.
