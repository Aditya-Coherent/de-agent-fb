# Five-Industry Examples — All Approaches

_Generated 2026-10-05T10:40:51+00:00 by `run_examples.py`. All values are USD Mn. Every approval gate was auto-approved as drafted (unattended demo run), so treat these as first drafts, not reviewed estimates._

| Market | Vertical | 1 Desk Research | 2A Demand: country total | 2B Demand: use case × country | 3 Supply: companies | 3 Supply: web evidence |
|---|---|---|---|---|---|---|
| Disposable Medical Gloves | Healthcare | 15,250 (2025) | 13,800 (2025) ⚠ 1 critical flag(s) | failed | pending rerun | pending rerun |
| Olive Oil | Food & Beverages | 14,615 (2025) | 10,496 (2025) | 9,257 (2025) ⚠ 2 critical flag(s) | 8,208 | 14,000 |
| Urea Fertilizer | Agriculture & Chemicals | 31,242 (2025) | 55,315 (2025) | pending rerun | pending rerun | pending rerun |

⚠ = the run raised critical quality flags (for example a price quoted per box applied to a count of single items); that total is not usable until the flagged inputs are corrected. Details under each market.

2C (product type × application × country) is not implemented, so it has no column.

## Disposable Medical Gloves (Healthcare)

Files: `approaches\examples\disposable-medical-gloves/`

- **2A:** global 13,800 (base year 2025); North America 4,750, Latin America 650, Europe 2,100, Asia Pacific 3,300, Rest of World 3,000
  - Driver: National apparent consumption of disposable medical gloves
  - Evidence: verified 7%, derived 0%, unverified 0%, assumption 93%
  - By Type (top_down, web_evidence_backed): Natural Rubber Gloves 2,987, Nitrile Gloves 6,060, Vinyl Gloves 2,294, Neoprene Gloves 625, Polyethylene Gloves 1,282, Others 552
  - By Application (top_down, web_evidence_backed): Examination 10,133, Surgical 3,667
  - By Form (top_down, web_evidence_backed): Powdered 1,030, Non-powdered 12,770
  - ⚠ [critical] target_commodity_unit_mismatch: One or more bucket(s) have a final_demand_unit that shares no words with the market's own target-commodity unit: ["'Total Disposable Medical Gloves demand': final_demand_unit 'Units' shares no words with the market's own target-commodity unit ('gloves (single pieces)') -- this usually means the bucket's final quantity is still denominated in a downstream/
  - Quality warnings: 2 (see result.json)
- **2B:** failed RuntimeError: LLM call failed after 3 attempts: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 5967ec99-1b2c-4a7a-bb05-f493e3a715a3)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}
- **1 Desk Research:** ok — global 15,250 (2025), CAGR 16.6%
- **3 Supply-Side:** pending rerun — companies —, web evidence —

## Olive Oil (Food & Beverages)

Files: `approaches\examples\olive-oil/`

- **2A:** global 10,496 (base year 2025); North America 1,600, Latin America 420, Europe 7,333, Asia Pacific 428, Middle East & Africa 714
  - Driver: Apparent domestic consumption of olive oil
  - Evidence: verified 20%, derived 0%, unverified 0%, assumption 80%
  - By Product Type (top_down, web_evidence_backed): Extra Virgin Olive Oil 6,144, Virgin Olive Oil 1,470, Refined Olive Oil 2,024, Olive Pomace Oil 858
  - By Application / End-Use (top_down, web_evidence_backed): Retail (Home Cooking & Table Use) 5,830, Foodservice 1,501, Industrial Food Manufacturing 1,939, Cosmetics & Personal Care 840, Pharmaceuticals 385
  - By Distribution Channel (top_down, assumption): Supermarkets/Hypermarkets 4,520, Specialty Stores 1,411, Online Retail 1,089, Foodservice Direct 2,036, Industrial Direct 1,439
  - By Form (top_down, web_evidence_backed): Packaged (Retail-Ready) 6,312, Bulk (Unpackaged) 4,184
  - Quality warnings: 2 (see result.json)
- **2B:** global 9,257 (base year 2025); North America 1,477, Latin America 145, Europe 6,782, Asia Pacific 185, Middle East & Africa 640, Rest of World 28
  - Use cases: Home Cooking and Table Use, Foodservice and Restaurant Operations, Processed Food Manufacturing, Infused and Flavored Olive Oil Production, Nutraceutical and Pharmaceutical Applications
  - Dropped as out of scope: Cosmetic and Personal Care Applications
  - Evidence: verified 0%, derived 0%, unverified 0%, assumption 100%
  - By Application (bottom-up) (bottom_up, see 02_sizing build-up rows): Home Cooking and Table Use 343, Foodservice and Restaurant Operations 5,948, Processed Food Manufacturing 2,604, Infused and Flavored Olive Oil Production 306, Nutraceutical and Pharmaceutical Applications 57
  - By Product Grade (top_down, web_evidence_backed): Extra Virgin Olive Oil 5,037, Virgin Olive Oil 1,433, Refined Olive Oil (including blends with virgin) 2,081, Olive-Pomace Oil 706
  - By Extraction Method (top_down, web_evidence_backed): Mechanical Extraction (Cold-Pressed / Cold-Extracted) 5,959, Mechanical Extraction (Hot-Pressed / Standard) 2,358, Chemical Extraction (Refining and Solvent Extraction) 940
  - By Distribution Channel (top_down, web_evidence_backed): Supermarkets and Hypermarkets 4,607, Specialty Food Stores and Gourmet Retailers 1,133, Online Retail 949, Foodservice and Institutional 1,609, Direct from Producer / Bulk Sales 960
  - By Packaging Format (top_down, web_evidence_backed): Glass Bottles and Jars 4,179, Plastic Bottles and Containers 1,700, Metal Cans and Tins 1,149, Bulk (Drums, IBCs, Flexitanks) 2,228
  - ⚠ [critical] dominant_bucket_unit_mismatch_risk: One usage bucket dominates the total, a symptom of a driver/price unit mismatch: 'Foodservice and Restaurant Operations' alone is 64% of the total across 5 bucket(s) -- check its driver/price for a unit mismatch before trusting this run.
  - ⚠ [critical] magnitude_cross_validation_discrepancy: 1 magnitude/cross-source discrepancy flag(s) across ['driver_magnitude'] -- flagged for review, original value(s)/source(s) unchanged: ["'Processed Food Manufacturing' driver total"]
  - Quality warnings: 6 (see result.json)
- **1 Desk Research:** ok — global 14,615 (2025), CAGR 4.0%
- **3 Supply-Side:** ok — companies 8,208, web evidence 14,000; top shares: Aceites del Sur 14%, Sovena 12%, Deoleo 10%, Gallo 5%, Borges International Group 4%

## Urea Fertilizer (Agriculture & Chemicals)

Files: `approaches\examples\urea-fertilizer/`

- **2A:** global 55,315 (base year 2025); North America 3,850, Latin America 1,190, Europe 2,800, Asia Pacific 29,625, Middle East & Africa 2,100, Rest of World 15,750
  - Driver: National urea fertilizer apparent consumption volume
  - Evidence: verified 13%, derived 13%, unverified 0%, assumption 73%
  - By Product Form (top_down, web_evidence_backed): Granular Urea 30,195, Prilled Urea 17,927, Liquid Urea 3,152, Urea Briquettes 2,545, Others 1,496
  - By Crop Type (top_down, web_evidence_backed): Cereals and Grains 30,385, Oilseeds and Pulses 7,919, Fruits and Vegetables 9,727, Commercial Crops 5,071, Others 2,213
  - By Fertilizers Technology (top_down, web_evidence_backed): Uncoated and Untreated Urea 37,868, Neem Coated Urea 3,622, Sulfur Coated Urea 4,139, Polymer-Coated Urea 6,667, Others 3,018
  - By End-use Application (top_down, web_evidence_backed): Field Crop Fertilization 33,744, Horticulture Crop Fertilization 6,026, Plantation Crop Fertilization 4,796, Pasture and Forage Fertilization 3,196, Fertilizers Blending 5,299, Others 2,255
  - By Distribution Channel (top_down, web_evidence_backed): Direct Sales 18,748, Indirect Sales 36,567
  - Quality warnings: 2 (see result.json)
- **2B:** pending rerun
- **1 Desk Research:** ok — global 31,242 (2025), CAGR 4.7%
- **3 Supply-Side:** pending rerun — companies —, web evidence —
