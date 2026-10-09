# Five-Industry Examples — All Approaches

_Generated 2026-10-08T14:13:42+00:00 by `run_examples.py`. All values are USD Mn. Every approval gate was auto-approved as drafted (unattended demo run), so treat these as first drafts, not reviewed estimates._

| Market | Vertical | 1 Desk Research | 2A Demand: country total | 2B Demand: use case × country | 3 Supply: companies | 3 Supply: web evidence |
|---|---|---|---|---|---|---|
| Disposable Medical Gloves | Healthcare | 15,250 (2025) | 13,800 (2025) ⚠ 1 critical flag(s) | 31,343 (2025) ⚠ 5 critical flag(s) | 24,732 | — |
| Gaming Consoles | Consumer Electronics | failed | — | — | — | — |
| Laptops | Consumer Electronics | failed | 630,850 (2025) | 306,726 (2025) ⚠ 3 critical flag(s) | 320,580 | 129,900 |
| Olive Oil | Food & Beverages | 14,615 (2025) | 15,556 (2025) | 23,655 (2025) ⚠ 2 critical flag(s) | 12,651 | 15,670 |
| Smartphones | Consumer Electronics | 557,000 (2025) | 272,825 (2025) | 465,072 (2025) ⚠ 4 critical flag(s) | 226,396 | 100 |
| Smartwatches | Consumer Electronics | 42,000 (2025) | 22,875 (2025) | 26,399 (2025) ⚠ 3 critical flag(s) | 65,359 | 7,500 |
| Tablets | Consumer Electronics | failed | 29,160 (2025) ⚠ 1 critical flag(s) | 48,096 (2025) ⚠ 3 critical flag(s) | 88,435 | — |
| Televisions | Consumer Electronics | 7,800 (2025) | 64,790 (2025) | 90,368 (2025) ⚠ 3 critical flag(s) | 98,929 | — |
| Urea Fertilizer | Agriculture & Chemicals | 31,242 (2025) | 55,315 (2025) | pending rerun | pending rerun | pending rerun |
| Watches | Consumer Electronics | 69,100 (2025) | 114,825 (2025) ⚠ 1 critical flag(s) | 40,358 (2025) ⚠ 1 critical flag(s) | 77,984 | 118,900 |
| Wireless Earbuds | Consumer Electronics | failed | failed | failed | failed | failed |

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
- **2B:** global 31,343 (base year 2025); North America 2,129, Latin America 403, Europe 1,614, Asia Pacific 25,272, Rest of World 1,924
  - Use cases: Surgical Procedures, Clinical Examination & Patient Care, Diagnostic & Pathology Laboratories, Dental & Oral Care, Emergency, EMS & First Response, Home Healthcare & Non-Acute Clinical Care, Pharmacy & Clinical Compounding
  - Evidence: verified 0%, derived 0%, unverified 0%, assumption 100%
  - By Application (bottom-up) (bottom_up, see 02_sizing build-up rows): Surgical Procedures 21,023, Clinical Examination & Patient Care 4,868, Diagnostic & Pathology Laboratories 388, Dental & Oral Care 1,293, Emergency, EMS & First Response 516, Home Healthcare & Non-Acute Clinical Care 3,051, Pharmacy & Clinical Compounding 204
  - By Type (top_down, web_evidence_backed): Natural Rubber Gloves 8,872, Nitrile Gloves 13,246, Vinyl Gloves 5,358, Neoprene Gloves 741, Polyethylene Gloves 2,498, Others 627
  - By Form (top_down, web_evidence_backed): Powdered 3,356, Non-powdered 27,987
  - ⚠ [critical] dominant_bucket_unit_mismatch_risk: One usage bucket dominates the total, a symptom of a driver/price unit mismatch: 'Surgical Procedures' alone is 67% of the total across 7 bucket(s) -- check its driver/price for a unit mismatch before trusting this run.
  - ⚠ [critical] country_cell_outlier: At least one country's value within a bucket is a wild outlier vs. its peers -- likely a compounding driver/intensity assumption error: 'Surgical Procedures'@China ($20,400.0M) is 800x this bucket's median country value -- check for a driver/intensity assumption blowup.
  - ⚠ [critical] target_commodity_unit_mismatch: One or more bucket(s) have a final_demand_unit that shares no words with the market's own target-commodity unit: ["'Surgical Procedures': final_demand_unit 'Units' shares no words with the market's own target-commodity unit ('gloves (single pieces)') -- this usually means the bucket's final quantity is still denominated in a downstream/adjacent product's 
  - ⚠ [critical] driver_magnitude_implausible: 1 driver(s) diverge from an independent reference figure by 4.1x or more -- flagged for review, the original value/source/note are unchanged: ['Dental & Oral Care']
  - ⚠ [critical] magnitude_cross_validation_discrepancy: 3 magnitude/cross-source discrepancy flag(s) across ['driver_magnitude', 'year_period_consistency'] -- flagged for review, original value(s)/source(s) unchanged: ["'Surgical Procedures' driver total", "'Dental & Oral Care' driver total", 'Emergency, EMS & First Response']
  - Quality warnings: 6 (see result.json)
- **1 Desk Research:** ok — global 15,250 (2025), CAGR 16.6%
- **3 Supply-Side:** ok — companies 24,732, web evidence —; top shares: Cardinal Health, Inc. 21%, Medline Industries, LP 6%, INTCO Medical Technology Co., Ltd. 6%, Top Glove Corporation Bhd 3%, Ansell Limited 3%

## Gaming Consoles (Consumer Electronics)

Files: `approaches\examples\gaming-consoles/`

- **2A:** not run
- **2B:** not run
- **1 Desk Research:** failed — global — (2026) (ERROR: could not build a DB skeleton for 'Gaming Consoles'. Try --skeleton <reference.xlsx> instead. |  | Loading weights:   0%|          | 0/103 [00:00<?, ?it/s] | Loading weights: 100%|██████████| 103/103 [00:00<00:00, 6465.91it/s])
- **3 Supply-Side:** not run — companies —, web evidence —

## Laptops (Consumer Electronics)

Files: `approaches\examples\laptops/`

- **2A:** global 630,850 (base year 2025); North America 96,250, Latin America 22,000, Europe 39,600, Asia Pacific 308,000, Rest of World 165,000
  - Driver: National smartphone shipments (apparent consumption)
  - Evidence: verified 0%, derived 0%, unverified 0%, assumption 100%
  - By Product Type (top_down, web_evidence_backed): Traditional Clamshell Laptops 418,231, 2-in-1 Convertibles 187,385, Dual-Screen Laptops 25,234
  - By Application / End-Use (top_down, web_evidence_backed): Personal / Consumer 298,952, Commercial / Enterprise 204,875, Education 88,803, Government 38,220
  - By Distribution Channel (top_down, web_evidence_backed): Online Retail 274,582, Offline Retail 200,744, Direct Sales (B2B) 115,472, OEM / System Integrators 40,051
  - By Screen Size (top_down, web_evidence_backed): Less than 13 inches 132,182, 13 to 15.9 inches 345,306, 16 inches and above 153,362
  - Quality warnings: 4 (see result.json)
- **2B:** global 306,726 (base year 2025); North America 33,253, Latin America 6,752, Europe 21,265, Asia Pacific 50,991, Rest of World 194,465
  - Use cases: Consumer Personal Use, Business & Enterprise, Education, Government & Public Sector, Gaming & Esports, Content Creation & Creative Professional
  - Evidence: verified 0%, derived 3%, unverified 19%, assumption 78%
  - By Application (bottom-up) (bottom_up, see 02_sizing build-up rows): Consumer Personal Use 92,274, Business & Enterprise 171,366, Education 5,912, Government & Public Sector 8,363, Gaming & Esports 18,762, Content Creation & Creative Professional 10,048
  - By Product Type (top_down, web_evidence_backed): Traditional Clamshell Laptops 189,837, 2-in-1 Convertible Laptops 107,688, Dual-Screen Laptops 9,202
  - By Price Tier (top_down, web_evidence_backed): Entry-Level (Under $500) 84,046, Mid-Range ($500-$999) 120,911, High-End ($1000-$1499) 63,286, Premium ($1500 and Above) 38,483
  - By Distribution Channel (top_down, web_evidence_backed): Online Retail 103,381, Offline Retail 97,762, Direct Sales (B2B) 71,302, Education and Government Contracts 34,282
  - By Screen Size (top_down, web_evidence_backed): Less than 13 inches 37,984, 13 to 14.9 inches 121,256, 15 to 16.9 inches 104,118, 17 inches and Above 43,368
  - ⚠ [critical] country_cell_outlier: At least one country's value within a bucket is a wild outlier vs. its peers -- likely a compounding driver/intensity assumption error: 'Consumer Personal Use'@Rest of World ($44,814.0M) is 15x this bucket's median country value -- check for a driver/intensity assumption blowup.; 'Business & Enterprise'@Rest of World ($144,066.0M) is 44x this bucket's median countr
  - ⚠ [critical] driver_magnitude_implausible: 1 driver(s) diverge from an independent reference figure by 5.81x or more -- flagged for review, the original value/source/note are unchanged: ['Business & Enterprise']
  - ⚠ [critical] magnitude_cross_validation_discrepancy: 2 magnitude/cross-source discrepancy flag(s) across ['driver_magnitude', 'year_period_consistency'] -- flagged for review, original value(s)/source(s) unchanged: ["'Business & Enterprise' driver total", 'Content Creation & Creative Professional']
  - Quality warnings: 7 (see result.json)
- **1 Desk Research:** failed — global — (2026) (ERROR: could not build a DB skeleton for 'Laptops'. Try --skeleton <reference.xlsx> instead. |  | Loading weights:   0%|          | 0/103 [00:00<?, ?it/s] | Loading weights: 100%|██████████| 103/103 [00:00<00:00, 3100.34it/s])
- **3 Supply-Side:** ok — companies 320,580, web evidence 129,900; top shares: HP Inc. 11%, Apple Inc. 9%, Quanta Computer 8%, Intel Corporation 6%, Compal Electronics 4%

## Olive Oil (Food & Beverages)

Files: `approaches\examples\olive-oil/`

- **2A:** global 15,556 (base year 2025); North America 1,932, Latin America 446, Europe 7,123, Asia Pacific 234, Middle East & Africa 1,623, Rest of World 4,198
  - Driver: Apparent consumption (FAOSTAT Food Balances: Olive Oil)
  - Evidence: verified 0%, derived 87%, unverified 0%, assumption 13%
  - By Product Type (top_down, web_evidence_backed): Extra Virgin Olive Oil 7,574, Virgin Olive Oil 3,537, Refined Olive Oil 2,899, Blended/Refined Olive Oil 1,547
  - By Application (top_down, web_evidence_backed): Food and Beverage 10,932, Cosmetics and Personal Care 2,082, Pharmaceuticals 907, Other Industrial Uses 1,635
  - By Distribution Channel (top_down, web_evidence_backed): Supermarkets/Hypermarkets 7,606, Specialty Stores 1,974, Online Retail 1,349, Food Service 2,685, Direct/Industrial 1,942
  - Quality warnings: 2 (see result.json)
- **2B:** global 23,655 (base year 2025); North America 3,440, Latin America 897, Europe 10,937, Asia Pacific 1,212, Middle East & Africa 1,408, Rest of World 5,762
  - Use cases: Household culinary use, Foodservice and hospitality, Food processing and manufacturing, Personal care and cosmetic formulations, Nutraceutical and pharmaceutical use
  - Evidence: verified 0%, derived 1%, unverified 0%, assumption 99%
  - By Application (bottom-up) (bottom_up, see 02_sizing build-up rows): Household culinary use 17,245, Foodservice and hospitality 4,893, Food processing and manufacturing 155, Personal care and cosmetic formulations 708, Nutraceutical and pharmaceutical use 653
  - By Product Grade (top_down, web_evidence_backed): Extra Virgin Olive Oil 12,709, Virgin Olive Oil 3,823, Refined Olive Oil 4,861, Blended/Refined Olive Oil (not elsewhere specified) 2,262
  - By Distribution Channel (top_down, web_evidence_backed): Retail 10,347, Foodservice 4,337, Food Processing 5,135, Bulk/Industrial 3,836
  - By Packaging Format (top_down, web_evidence_backed): Bottles (Glass/Plastic) 13,336, Cans/Tins 4,497, Jars 1,142, Bulk Containers (Drums, IBCs, Flexitanks) 4,680
  - By Source/Origin (top_down, web_evidence_backed): Conventional 20,417, Organic 3,238
  - ⚠ [critical] dominant_bucket_unit_mismatch_risk: One usage bucket dominates the total, a symptom of a driver/price unit mismatch: 'Household culinary use' alone is 73% of the total across 5 bucket(s) -- check its driver/price for a unit mismatch before trusting this run.
  - ⚠ [critical] magnitude_cross_validation_discrepancy: 1 magnitude/cross-source discrepancy flag(s) across ['intensity_magnitude'] -- flagged for review, original value(s)/source(s) unchanged: ["'Household culinary use' intensity"]
  - Quality warnings: 12 (see result.json)
- **1 Desk Research:** ok — global 14,615 (2025), CAGR 4.0%
- **3 Supply-Side:** ok — companies 12,651, web evidence 15,670; top shares: Acesur 8%, Deoleo 6%, Dcoop Group 6%, Sovena 6%, Salov Group 4%

## Smartphones (Consumer Electronics)

Files: `approaches\examples\smartphones/`

- **2A:** global 272,825 (base year 2025); North America 44,100, Latin America 10,500, Europe 18,200, Asia Pacific 193,725, Middle East & Africa 6,300
  - Driver: National smartphone shipments (apparent consumption)
  - Evidence: verified 7%, derived 13%, unverified 53%, assumption 27%
  - By Operating System (top_down, web_evidence_backed): Android 215,945, iOS 56,880
  - By Distribution Channel (top_down, web_evidence_backed): OEM 62,613, Retailer 117,430, E-commerce 92,782
  - Quality warnings: 2 (see result.json)
- **2B:** global 465,072 (base year 2025); North America 44,947, Latin America 22,912, Europe 45,472, Asia Pacific 201,986, Rest of World 149,754
  - Use cases: Personal consumer smartphones, Enterprise & business-issued smartphones, Premium/flagship smartphones, Mid-range smartphones, Entry-level/budget smartphones
  - Dropped as out of scope: Refurbished/renewed smartphones
  - Evidence: verified 4%, derived 5%, unverified 39%, assumption 52%
  - By Application (bottom-up) (bottom_up, see 02_sizing build-up rows): Personal consumer smartphones 320,171, Enterprise & business-issued smartphones 28,328, Premium/flagship smartphones 47,563, Mid-range smartphones 47,851, Entry-level/budget smartphones 21,158
  - By Operating System (top_down, web_evidence_backed): Android 358,205, iOS 106,867
  - By Distribution Channel (top_down, assumption): OEM 122,455, Retailer 168,189, E-commerce 174,429
  - ⚠ [critical] dominant_bucket_unit_mismatch_risk: One usage bucket dominates the total, a symptom of a driver/price unit mismatch: 'Personal consumer smartphones' alone is 69% of the total across 5 bucket(s) -- check its driver/price for a unit mismatch before trusting this run.
  - ⚠ [critical] country_cell_outlier: At least one country's value within a bucket is a wild outlier vs. its peers -- likely a compounding driver/intensity assumption error: 'Personal consumer smartphones'@Rest of World ($149,754.5M) is 21x this bucket's median country value -- check for a driver/intensity assumption blowup.; 'Enterprise & business-issued smartphones'@China ($22,947.1M) is 18x this buc
  - ⚠ [critical] driver_magnitude_implausible: 1 driver(s) diverge from an independent reference figure by 8.86x or more -- flagged for review, the original value/source/note are unchanged: ['Entry-level/budget smartphones']
  - ⚠ [critical] magnitude_cross_validation_discrepancy: 3 magnitude/cross-source discrepancy flag(s) across ['driver_magnitude'] -- flagged for review, original value(s)/source(s) unchanged: ["'Enterprise & business-issued smartphones' driver total", "'Premium/flagship smartphones' driver total", "'Entry-level/budget smartphones' driver total"]
  - Quality warnings: 10 (see result.json)
- **1 Desk Research:** ok — global 557,000 (2025), CAGR 6.8%
- **3 Supply-Side:** ok — companies 226,396, web evidence 100; top shares: Foxconn Technology Group 40%, Honor Device Co., Ltd. 5%, BOE Technology Group 5%, Lenovo Group Limited 4%, FIH Mobile Limited 2%

## Smartwatches (Consumer Electronics)

Files: `approaches\examples\smartwatches/`

- **2A:** global 22,875 (base year 2025); North America 8,850, Latin America 750, Europe 3,075, Asia Pacific 6,450, Rest of World 3,750
  - Driver: National watch consumption volume (apparent consumption)
  - Evidence: verified 0%, derived 0%, unverified 27%, assumption 73%
  - By Product Type (top_down, web_evidence_backed): Smartwatch with Cellular Connectivity 7,382, Smartwatch without Cellular Connectivity 15,493
  - By Application (top_down, web_evidence_backed): Fitness & Health Tracking 11,239, Communication & Notifications 4,926, Lifestyle & Fashion 3,677, Safety & Security 1,826, Other Applications 1,208
  - By Distribution Channel (top_down, web_evidence_backed): Online Retail 14,195, Offline Retail 7,582, Direct Sales 1,099
  - By Operating System (top_down, web_evidence_backed): watchOS 6,521, Wear OS 5,414, Tizen 608, RTOS-based 8,641, Other Operating Systems 1,691
  - Quality warnings: 3 (see result.json)
- **2B:** global 26,399 (base year 2025); North America 5,071, Latin America 500, Europe 2,109, Asia Pacific 15,665, Rest of World 3,054
  - Use cases: General-purpose consumer smartwatches, Fitness & outdoor sports smartwatches, Health-monitoring & medical smartwatches, Kids' & senior safety smartwatches, Enterprise & industrial smartwatches
  - Evidence: verified 1%, derived 1%, unverified 28%, assumption 69%
  - By Application (bottom-up) (bottom_up, see 02_sizing build-up rows): General-purpose consumer smartwatches 14,931, Fitness & outdoor sports smartwatches 2,115, Health-monitoring & medical smartwatches 8,001, Kids' & senior safety smartwatches 680, Enterprise & industrial smartwatches 672
  - By Product Type (top_down, web_evidence_backed): Classic Smartwatches 14,488, Standalone/Cellular Smartwatches 6,197, Hybrid Smartwatches 1,679, Kids Smartwatches 4,034
  - By Price Band (top_down, web_evidence_backed): Entry-Level (Under $100) 6,545, Mid-Range ($100-$299) 10,548, Premium ($300-$599) 6,636, Luxury ($600 and Above) 2,670
  - By Operating System (top_down, web_evidence_backed): watchOS 5,833, Wear OS 4,870, Tizen 1,021, RTOS-Based 9,646, Other Proprietary OS 5,030
  - By Display Technology (top_down, web_evidence_backed): LCD 6,551, OLED/AMOLED 17,220, E-Ink 1,314, Other Display Technologies 1,314
  - ⚠ [critical] country_cell_outlier: At least one country's value within a bucket is a wild outlier vs. its peers -- likely a compounding driver/intensity assumption error: 'General-purpose consumer smartwatches'@China ($7,560.0M) is 20x this bucket's median country value -- check for a driver/intensity assumption blowup.; 'Kids' & senior safety smartwatches'@China ($504.0M) is 27x this bucket's media
  - ⚠ [critical] driver_magnitude_implausible: 1 driver(s) diverge from an independent reference figure by 4.32x or more -- flagged for review, the original value/source/note are unchanged: ['General-purpose consumer smartwatches']
  - ⚠ [critical] magnitude_cross_validation_discrepancy: 1 magnitude/cross-source discrepancy flag(s) across ['driver_magnitude'] -- flagged for review, original value(s)/source(s) unchanged: ["'Fitness & outdoor sports smartwatches' driver total"]
  - Quality warnings: 5 (see result.json)
- **1 Desk Research:** ok — global 42,000 (2025), CAGR 13.2%
- **3 Supply-Side:** ok — companies 65,359, web evidence 7,500; top shares: Apple Inc. 55%, Huawei Technologies 2%, Xiaomi Corporation 2%, Fitbit 1%, Alphabet Inc. 1%

## Tablets (Consumer Electronics)

Files: `approaches\examples\tablets/`

- **2A:** global 29,160 (base year 2025); North America 486, Latin America 0, Europe 1,764, Asia Pacific 6,336, Rest of World 20,574
  - Driver: Annual tablet unit shipments (apparent consumption)
  - Evidence: verified 0%, derived 13%, unverified 13%, assumption 73%
  - By Product Type (top_down, web_evidence_backed): Slate Tablets 17,092, 2-in-1 Detachable Tablets 6,236, Rugged Tablets 3,398, Mini Tablets 2,434
  - By Application (top_down, web_evidence_backed): Consumer 13,222, Commercial 5,644, Education 5,828, Healthcare 1,806, Industrial 1,474, Government 1,185
  - By Distribution Channel (top_down, web_evidence_backed): Online Retail 12,424, Offline Retail 12,542, Direct Sales 2,298, Telecom Operators 1,896
  - By Screen Size (top_down, web_evidence_backed): Less than 8 inches 2,705, 8 to 10 inches 13,467, 10 to 12 inches 9,861, 12 inches and above 3,127
  - ⚠ [critical] country_cell_outlier: At least one country's value within a bucket is a wild outlier vs. its peers -- likely a compounding driver/intensity assumption error: 'Total Tablets demand'@Rest of World ($20,574.0M) is 42x this bucket's median country value -- check for a driver/intensity assumption blowup.
  - Quality warnings: 4 (see result.json)
- **2B:** global 48,096 (base year 2025); North America 13,579, Latin America 742, Europe 3,818, Asia Pacific 11,910, Rest of World 18,048
  - Use cases: Consumer Personal Use, Business & Enterprise Work, Education & Academic Learning, Healthcare & Clinical Use, Creative & Professional Media, Government & Public Sector
  - Evidence: verified 0%, derived 1%, unverified 16%, assumption 83%
  - By Application (bottom-up) (bottom_up, see 02_sizing build-up rows): Consumer Personal Use 34,020, Business & Enterprise Work 6,014, Education & Academic Learning 3,528, Healthcare & Clinical Use 1,798, Creative & Professional Media 1,436, Government & Public Sector 1,299
  - By Product Type (top_down, web_evidence_backed): Slate Tablets 29,078, Detachable Tablets 14,783, Convertible Tablets 4,235
  - By Operating System (top_down, assumption): iOS/iPadOS 18,960, Android 24,009, Windows 4,096, Others (e.g., Chrome OS, Linux) 1,032
  - By Screen Size (top_down, web_evidence_backed): 7 to 8 inches 3,154, 8 to 9 inches 5,433, 9 to 10 inches 10,328, 10 to 11 inches 18,122, 11 inches and above 11,060
  - By Distribution Channel (top_down, web_evidence_backed): Online Retail 28,741, Offline Retail 15,170, Direct Sales (e.g., OEM websites, enterprise contracts) 4,185
  - ⚠ [critical] dominant_bucket_unit_mismatch_risk: One usage bucket dominates the total, a symptom of a driver/price unit mismatch: 'Consumer Personal Use' alone is 71% of the total across 6 bucket(s) -- check its driver/price for a unit mismatch before trusting this run.
  - ⚠ [critical] country_cell_outlier: At least one country's value within a bucket is a wild outlier vs. its peers -- likely a compounding driver/intensity assumption error: 'Education & Academic Learning'@United States ($2,310.0M) is 16x this bucket's median country value -- check for a driver/intensity assumption blowup.
  - ⚠ [critical] magnitude_cross_validation_discrepancy: 2 magnitude/cross-source discrepancy flag(s) across ['driver_magnitude'] -- flagged for review, original value(s)/source(s) unchanged: ["'Business & Enterprise Work' driver total", "'Education & Academic Learning' driver total"]
  - Quality warnings: 5 (see result.json)
- **1 Desk Research:** failed — global — (2026) (ERROR: could not build a DB skeleton for 'Tablets'. Try --skeleton <reference.xlsx> instead. |  | Loading weights:   0%|          | 0/103 [00:00<?, ?it/s] | Loading weights: 100%|██████████| 103/103 [00:00<00:00, 11018.78it/s])
- **3 Supply-Side:** ok — companies 88,435, web evidence —; top shares: Apple Inc. 34%, Foxconn Technology Group 11%, Amazon.com, Inc. 5%, Pegatron Corporation 5%, Microsoft Corporation 4%

## Televisions (Consumer Electronics)

Files: `approaches\examples\televisions/`

- **2A:** global 64,790 (base year 2025); North America 9,900, Latin America 2,420, Europe 7,590, Asia Pacific 39,380, Rest of World 5,500
  - Driver: National watch consumption volume (apparent consumption)
  - Evidence: verified 0%, derived 0%, unverified 0%, assumption 100%
  - By Display Technology (top_down, web_evidence_backed): LCD (LED-backlit) 38,844, OLED 7,028, QLED 14,000, MicroLED 763, Other (e.g., Plasma, CRT) 4,155
  - By Resolution (top_down, web_evidence_backed): HD (720p/1080p) 21,432, 4K UHD 40,742, 8K UHD 2,616
  - By Application (top_down, assumption): Residential 52,762, Commercial (Hospitality, Retail Signage, Corporate AV) 12,028
  - By Distribution Channel (top_down, web_evidence_backed): Online Retail 28,952, Offline Retail 32,598, Direct Sales (B2B) 3,240
  - Quality warnings: 3 (see result.json)
- **2B:** global 90,368 (base year 2025); North America 10,326, Latin America 6,253, Europe 10,431, Asia Pacific 30,710, Rest of World 32,648
  - Use cases: Household / Residential, Hospitality & Lodging, Food, Beverage & Retail Venues, Corporate, Education & Institutional, Broadcast, Media & Professional Production, Other Commercial & Public Display
  - Evidence: verified 0%, derived 4%, unverified 8%, assumption 88%
  - By Application (bottom-up) (bottom_up, see 02_sizing build-up rows): Household / Residential 69,231, Hospitality & Lodging 8,112, Food, Beverage & Retail Venues 1,980, Corporate, Education & Institutional 4,465, Broadcast, Media & Professional Production 2,094, Other Commercial & Public Display 4,486
  - By Display Technology (top_down, web_evidence_backed): LCD (including LED-backlit) 62,887, OLED 7,755, QLED 15,208, Other (e.g., plasma, CRT, projection TV) 4,518
  - By Screen Size (top_down, web_evidence_backed): Below 32 inches 14,421, 32 to 45 inches 27,574, 46 to 55 inches 23,095, 56 to 65 inches 16,001, 66 inches and above 9,278
  - By Distribution Channel (top_down, web_evidence_backed): Online retail 33,911, Offline retail 50,333, Direct sales (commercial) 6,124
  - ⚠ [critical] dominant_bucket_unit_mismatch_risk: One usage bucket dominates the total, a symptom of a driver/price unit mismatch: 'Household / Residential' alone is 77% of the total across 6 bucket(s) -- check its driver/price for a unit mismatch before trusting this run.
  - ⚠ [critical] country_cell_outlier: At least one country's value within a bucket is a wild outlier vs. its peers -- likely a compounding driver/intensity assumption error: 'Food, Beverage & Retail Venues'@China ($1,344.0M) is 29x this bucket's median country value -- check for a driver/intensity assumption blowup.; 'Broadcast, Media & Professional Production'@Brazil ($1,742.9M) is 83x this bucket's m
  - ⚠ [critical] magnitude_cross_validation_discrepancy: 3 magnitude/cross-source discrepancy flag(s) across ['driver_magnitude', 'year_period_consistency'] -- flagged for review, original value(s)/source(s) unchanged: ['Hospitality & Lodging', "'Corporate, Education & Institutional' driver total", "'Other Commercial & Public Display' driver total"]
  - Quality warnings: 5 (see result.json)
- **1 Desk Research:** ok — global 7,800 (2025), CAGR 3.2%
- **3 Supply-Side:** ok — companies 98,929, web evidence —; top shares: Samsung Display 11%, BOE Technology Group Co., Ltd. 11%, LG Display 11%, Sony Group Corporation 9%, TCL China Star Optoelectronics Technology (TCL CSOT) 6%

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

## Watches (Consumer Electronics)

Files: `approaches\examples\watches/`

- **2A:** global 114,825 (base year 2025); North America 13,750, Europe 11,825, Asia Pacific 56,000, Middle East & Africa 1,250, Rest of World 32,000
  - Driver: National watch consumption volume (apparent consumption)
  - Evidence: verified 7%, derived 0%, unverified 7%, assumption 87%
  - By Product Type (top_down, web_evidence_backed): Mechanical Watches 18,056, Quartz Watches 43,466, Smartwatches 47,404, Hybrid Watches 5,898
  - By Application (top_down, web_evidence_backed): Personal Use 40,326, Gifting 23,356, Professional Use 10,630, Sports and Fitness 18,492, Fashion and Status 22,020
  - By Distribution Channel (top_down, web_evidence_backed): Online Retail 37,835, Offline Retail 76,990
  - By Price Range (top_down, web_evidence_backed): Mass Market 37,412, Mid-Range 36,215, Premium 25,038, Luxury 16,160
  - ⚠ [critical] magnitude_cross_validation_discrepancy: 1 magnitude/cross-source discrepancy flag(s) across ['driver_magnitude'] -- flagged for review, original value(s)/source(s) unchanged: ["'Total Watches demand' driver total"]
  - Quality warnings: 2 (see result.json)
- **2B:** global 40,358 (base year 2025); North America 4,613, Europe 6,609, Asia Pacific 18,566, Middle East & Africa 1,269, Rest of World 9,302
  - Use cases: Everyday Timekeeping & Personal Accessory, Professional & Occupational Timing, Sports, Fitness & Outdoor Performance Tracking, Smartwatch Connectivity & Digital Life, Gifting & Milestone Commemoration
  - Evidence: verified 1%, derived 3%, unverified 19%, assumption 77%
  - By Application (bottom-up) (bottom_up, see 02_sizing build-up rows): Everyday Timekeeping & Personal Accessory 18,615, Professional & Occupational Timing 160, Sports, Fitness & Outdoor Performance Tracking 9,312, Smartwatch Connectivity & Digital Life 4,974, Gifting & Milestone Commemoration 7,298
  - By Product Type (top_down, web_evidence_backed): Mechanical Watches 6,964, Quartz Watches 12,803, Smartwatches 18,616, Hybrid Watches 1,975
  - By Distribution Channel (top_down, web_evidence_backed): Physical Retail 22,298, Brand Boutiques 5,032, Online 13,029
  - By Price Segment (top_down, web_evidence_backed): Mass Market 15,552, Mid-Range 12,095, Premium 7,547, Luxury 5,164
  - By Consumer Group (top_down, web_evidence_backed): Men 20,244, Women 14,060, Unisex 6,054
  - ⚠ [critical] magnitude_cross_validation_discrepancy: 3 magnitude/cross-source discrepancy flag(s) across ['driver_magnitude'] -- flagged for review, original value(s)/source(s) unchanged: ["'Everyday Timekeeping & Personal Accessory' driver total", "'Smartwatch Connectivity & Digital Life' driver total", "'Gifting & Milestone Commemoration' driver total"]
  - Quality warnings: 4 (see result.json)
- **1 Desk Research:** ok — global 69,100 (2025), CAGR 4.3%
- **3 Supply-Side:** ok — companies 77,984, web evidence 118,900; top shares: Apple Inc. 23%, LVMH Moët Hennessy Louis Vuitton 6%, Audemars Piguet 4%, Garmin Ltd. 4%, Samsung Electronics Co., Ltd. 4%

## Wireless Earbuds (Consumer Electronics)

Files: `approaches\examples\wireless-earbuds/`

- **2A:** failed RuntimeError: LLM call failed after 3 attempts: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: ee932d9f-d451-4a2d-86e4-4c1574143104)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}
- **2B:** failed RuntimeError: LLM call failed after 3 attempts: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 1928b892-35ba-44e5-8300-5f6df2faec18)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}}
- **1 Desk Research:** failed — global — (2026) (ERROR: could not build a DB skeleton for 'Wireless Earbuds'. Try --skeleton <reference.xlsx> instead. |  | Loading weights:   0%|          | 0/103 [00:00<?, ?it/s] | Loading weights: 100%|██████████| 103/103 [00:00<00:00, 6168.09it/s])
- **3 Supply-Side:** failed — companies —, web evidence — (    data = llm.complete_json(_GEOGRAPHY_SYSTEM, f"Market: {market_name}") |   File "C:\Users\aimlpc1\Desktop\de-agent-fb\src\me_engine\generation\llm.py", line 97, in complete_json |     raise RuntimeError(f"LLM call failed after {retries} attempts: {last_err}") | RuntimeError: LLM call failed after 3 attempts: Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: 371f9482-683b-4d4d-9616-87a5f06bbd7c)', 'type': 'unknown_error', 'param': None, 'code': 'invalid_request_error'}} | During task with name 'geography' and id '23ff961d-09c4-1e33-1328-8703135694a7')
