# Comparison vs actual (2025, US$ Mn, global)

% diff = (ours − actual) / actual. In range: ±10% (Desk/2A/2B); Supply 70–100% of actual.

**All markets**

| Approach | n | Median abs % error | Within range |
|---|---|---|---|
| Desk | 7 | 10.5% | 2 (within ±10%) |
| 2A | 9 | 39.8% | 3 (within ±10%) |
| 2B | 8 | 45.4% | 0 (within ±10%) |
| Supply | 8 | 57.5% | 2 (70-100% of actual) |

**Excluding "low" confidence**

| Approach | n | Median abs % error | Within range |
|---|---|---|---|
| Desk | 4 | 8.7% | 2 (within ±10%) |
| 2A | 6 | 46.6% | 2 (within ±10%) |
| 2B | 6 | 35.5% | 0 (within ±10%) |
| Supply | 6 | 57.5% | 1 (70-100% of actual) |

| Market | Vertical | Confidence | Actual 2025 (US$ Mn) | Desk | 2A | 2B | Supply | Desk % diff | 2A % diff | 2B % diff | Supply % diff | Supply % of actual | Best approach | Critical quality flags | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Smartphones | Consumer Electronics | ok | 585,630 | 557,000 | 272,825 | 465,072 | 226,396 | -4.9% | -53.4% | -20.6% | -61.3% | 39% | Desk | 2B: dominant_bucket_unit_mismatch_risk; 2B: country_cell_outlier; 2B: driver_magnitude_implausible; 2B: magnitude_cross_validation_discrepancy | ok |
| Watches | Consumer Electronics | low | 81,300 | 69,100 | 114,825 | 40,358 | 77,984 | -15.0% | +41.2% | -50.4% | -4.1% | 96% | Supply | 2A: magnitude_cross_validation_discrepancy; 2B: magnitude_cross_validation_discrepancy | ok |
| Smartwatches | Consumer Electronics | ok | 38,000 | 42,000 | 22,875 | 26,399 | 65,359 | +10.5% | -39.8% | -30.5% | +72.0% | 172% | Desk | 2B: country_cell_outlier; 2B: driver_magnitude_implausible; 2B: magnitude_cross_validation_discrepancy | ok |
| Laptops | Consumer Electronics | ok | 115,100 | — | 630,850 | 306,726 | 320,580 | — | +448.1% | +166.5% | +178.5% | 279% | 2B | 2B: country_cell_outlier; 2B: driver_magnitude_implausible; 2B: magnitude_cross_validation_discrepancy | pending rerun |
| Tablets | Consumer Electronics | ok | 64,000 | — | 29,160 | 48,096 | 88,435 | — | -54.4% | -24.9% | +38.2% | 138% | 2B | 2A: country_cell_outlier; 2B: dominant_bucket_unit_mismatch_risk; 2B: country_cell_outlier; 2B: magnitude_cross_validation_discrepancy | pending rerun |
| Televisions | Consumer Electronics | ok | 64,355 | 7,800 | 64,790 | 90,368 | 98,929 | -87.9% | +0.7% | +40.4% | +53.7% | 154% | 2A | 2B: dominant_bucket_unit_mismatch_risk; 2B: country_cell_outlier; 2B: magnitude_cross_validation_discrepancy | ok |
| Wireless Earbuds | Consumer Electronics | ok | 35,600 | — | — | — | — | — | — | — | — | — |  |  | pending rerun |
| Gaming Consoles | Consumer Electronics | low | 44,700 | — | — | — | — | — | — | — | — | — |  |  | pending rerun |
| Digital Cameras | Consumer Electronics | ok | 22,285 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Smart Speakers | Consumer Electronics | ok | 14,250 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Olive Oil | Food & Beverages | ok | 15,700 | 14,615 | 15,556 | 23,655 | 12,651 | -6.9% | -0.9% | +50.7% | -19.4% | 81% | 2A | 2B: dominant_bucket_unit_mismatch_risk; 2B: magnitude_cross_validation_discrepancy | ok |
| Coffee | Food & Beverages | low | 256,300 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Chocolate | Food & Beverages | ok | 167,000 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Bottled Water | Food & Beverages | ok | 323,070 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Beer | Food & Beverages | ok | 804,650 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Wine | Food & Beverages | ok | 360,360 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Vinegar | Food & Beverages | low | 12,200 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Breakfast Cereals | Food & Beverages | low | 42,830 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Plant-Based Milk | Food & Beverages | ok | 22,660 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Energy Drinks | Food & Beverages | ok | 77,160 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Tea | Food & Beverages | low | 26,752 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Disposable Medical Gloves | Healthcare | low | 13,800 | 15,250 | 13,800 | 31,343 | 24,732 | +10.5% | +0.0% | +127.1% | +79.2% | 179% | 2A | 2A: target_commodity_unit_mismatch; 2B: dominant_bucket_unit_mismatch_risk; 2B: country_cell_outlier; 2B: target_commodity_unit_mismatch; 2B: driver_magnitude_implausible; 2B: magnitude_cross_validation_discrepancy | ok |
| Insulin | Healthcare | ok | 29,200 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Contact Lenses | Healthcare | low | 18,880 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Hearing Aids | Healthcare | ok | 8,800 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Surgical Masks | Healthcare | ok | 5,060 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Dental Implants | Healthcare | ok | 5,640 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Wheelchairs | Healthcare | ok | 5,580 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Blood Glucose Monitoring Devices | Healthcare | ok | 17,220 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Orthopedic Implants | Healthcare | ok | 52,400 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Syringes | Healthcare | ok | 10,491 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Cardiac Pacemakers | Healthcare | ok | 5,660 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Automotive Tires | Automotive | low | 157,585 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Electric Vehicles | Automotive | low | 698,630 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Automotive Lead-Acid Batteries | Automotive | ok | 31,300 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Motorcycles | Automotive | low | 140,700 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Bicycles | Automotive | ok | 84,250 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Electric Scooters | Automotive | low | 45,791 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Automotive Lubricants | Automotive | ok | 75,080 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Car Rental | Automotive | low | 135,000 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Automotive Glass | Automotive | ok | 24,600 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Electric Vehicle Chargers | Automotive | low | 18,870 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Urea Fertilizer | Agriculture & Chemicals | low | 73,500 | 31,242 | 55,315 | — | — | -57.5% | -24.7% | — | — | — | 2A |  | pending rerun |
| Potash Fertilizer | Agriculture & Chemicals | ok | 29,560 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Organic Fertilizers | Agriculture & Chemicals | ok | 11,110 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Seeds | Agriculture & Chemicals | low | 63,440 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Crop Protection Chemicals | Agriculture & Chemicals | ok | 83,320 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Tractors | Agriculture & Chemicals | ok | 84,670 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Animal Feed | Agriculture & Chemicals | ok | 483,690 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Drip Irrigation | Agriculture & Chemicals | ok | 8,050 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Adhesives & Sealants | Chemicals & Materials | ME workbook | 78,341 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Adhesives & Bonding | Chemicals & Materials | ME workbook | 63,545 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Technical Foam | Chemicals & Materials | ME workbook | 47,251 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Titanium Dioxide | Chemicals & Materials | ok | 21,615 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Paints & Coatings | Chemicals & Materials | ok | 198,250 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Carbon Black | Chemicals & Materials | ok | 21,600 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Caustic Soda | Chemicals & Materials | ok | 46,500 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Activated Carbon | Chemicals & Materials | low | 6,845 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Polyethylene | Chemicals & Materials | low | 143,130 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Cosmetics | Consumer Goods | ok | 346,840 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Fragrances | Consumer Goods | ok | 60,000 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Shampoo | Consumer Goods | ok | 36,915 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Toothpaste | Consumer Goods | ok | 22,300 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Baby Diapers | Consumer Goods | low | 66,300 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Sunglasses | Consumer Goods | ok | 43,030 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Luxury Handbags | Consumer Goods | low | 35,830 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Mattresses | Consumer Goods | ok | 49,560 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Athletic Footwear | Consumer Goods | ok | 145,490 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Toys | Consumer Goods | ok | 121,750 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Razors | Consumer Goods | ok | 12,754 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Cloud Computing | Information & Communication Technology | ok | 781,270 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Cybersecurity | Information & Communication Technology | ok | 227,600 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Cyber-Physical Systems Security | Information & Communication Technology | ME workbook | 22,437 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Retail Self Scanning Solutions | Information & Communication Technology | ME workbook | 3,522 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Data Center Colocation | Information & Communication Technology | ok | 83,040 | — | — | — | — | — | — | — | — | — |  |  | pending |
| CRM Software | Information & Communication Technology | low | 96,255 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Semiconductors | Information & Communication Technology | ok | 794,300 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Smart Cards | Information & Communication Technology | low | 16,200 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Video Streaming | Information & Communication Technology | low | 159,980 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Spectral Sensor | Industrial & Electronics | ME workbook | 6,532 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Process Analyzer | Industrial & Electronics | ME workbook | 9,211 | — | — | — | — | — | — | — | — | — |  |  | pending |
| High Energy Laser | Industrial & Electronics | ME workbook | 24,810 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Solar Panels | Industrial & Electronics | low | 298,560 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Lithium-ion Batteries | Industrial & Electronics | low | 124,390 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Air Compressors | Industrial & Electronics | ok | 19,700 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Elevators | Industrial & Electronics | ok | 98,720 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Forklifts | Industrial & Electronics | ok | 78,780 | — | — | — | — | — | — | — | — | — |  |  | pending |
| LED Lighting | Industrial & Electronics | ok | 108,990 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Industrial Robots | Industrial & Electronics | low | 23,000 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Gel Ice Pack | Packaging | ME workbook | 972 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Corrugated Boxes | Packaging | ok | 195,600 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Glass Bottles | Packaging | low | 74,000 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Aluminum Cans | Packaging | ok | 60,330 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Flexible Packaging | Packaging | ok | 269,200 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Paper Cups | Packaging | ok | 13,400 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Labels | Packaging | low | 53,440 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Wind Turbines | Energy & Power | ok | 170,900 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Diesel Generators | Energy & Power | ok | 21,440 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Smart Meters | Energy & Power | ok | 32,550 | — | — | — | — | — | — | — | — | — |  |  | pending |
| Hydrogen Electrolyzers | Energy & Power | low | 2,030 | — | — | — | — | — | — | — | — | — |  |  | pending |
