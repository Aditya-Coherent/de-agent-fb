# Desk Research: refined code vs previous, all 63 markets (2025, US$ Mn, global)

% error vs the median of published values; in range = within [min, max] published ±5%; badly off = more than 25% from the actual. Previous results are frozen from DESK_EVAL.json (2026-10-09 runs). The refined runs are new live runs (2026-10-12), so part of every difference is web-search noise. The held-out set was not used to tune the refinements.

## Held-out (38 markets)

| Version | Median abs error | Mean abs error | In range | Badly off | n |
|---|---|---|---|---|---|
| Previous code, shipped defaults (offline replay) | 8.8% | 21.7% | 31/38 | 5 | 38 |
| Previous code, live run | 13.9% | 25.8% | 23/38 | 10 | 38 |
| Refined code, live run | 10.7% | 18.5% | 26/38 | 4 | 38 |

Same 38 markets in every version:

| Version | Median abs error | Mean abs error | In range | Badly off | n |
|---|---|---|---|---|---|
| Previous code, shipped defaults (offline replay) | 8.8% | 21.7% | 31/38 | 5 | 38 |
| Previous code, live run | 13.9% | 25.8% | 23/38 | 10 | 38 |
| Refined code, live run | 10.7% | 18.5% | 26/38 | 4 | 38 |

## Tuning (25 markets)

| Version | Median abs error | Mean abs error | In range | Badly off | n |
|---|---|---|---|---|---|
| Previous code, shipped defaults (offline replay) | 9.2% | 17.2% | 19/25 | 6 | 25 |
| Previous code, live run | 9.2% | 21.7% | 20/25 | 7 | 25 |
| Refined code, live run | 6.3% | 14.9% | 21/25 | 4 | 25 |

Same 25 markets in every version:

| Version | Median abs error | Mean abs error | In range | Badly off | n |
|---|---|---|---|---|---|
| Previous code, shipped defaults (offline replay) | 9.2% | 17.2% | 19/25 | 6 | 25 |
| Previous code, live run | 9.2% | 21.7% | 20/25 | 7 | 25 |
| Refined code, live run | 6.3% | 14.9% | 21/25 | 4 | 25 |

## All (63 markets)

| Version | Median abs error | Mean abs error | In range | Badly off | n |
|---|---|---|---|---|---|
| Previous code, shipped defaults (offline replay) | 9.2% | 20.0% | 50/63 | 11 | 63 |
| Previous code, live run | 13.0% | 24.2% | 43/63 | 17 | 63 |
| Refined code, live run | 9.7% | 17.1% | 47/63 | 8 | 63 |

Same 63 markets in every version:

| Version | Median abs error | Mean abs error | In range | Badly off | n |
|---|---|---|---|---|---|
| Previous code, shipped defaults (offline replay) | 9.2% | 20.0% | 50/63 | 11 | 63 |
| Previous code, live run | 13.0% | 24.2% | 43/63 | 17 | 63 |
| Refined code, live run | 9.7% | 17.1% | 47/63 | 8 | 63 |

## Does the confidence grade mean anything?

| Grade | Markets | Median abs error | In range |
|---|---|---|---|
| A | 23 | 6.6% | 19/23 |
| B | 15 | 7.0% | 12/15 |
| C | 8 | 6.7% | 5/8 |

## Every market

| Market | Set | Actual | Published range | Prev shipped | Err | In range | Prev live | Err | In range | Refined | Err | In range | Conf. | Cited range | Figures | Flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Animal Feed | heldout | 483,690 | 420,820–490,830 | 510,000 | +5.4% | yes | 420,820 | -13.0% | yes | 548,220 | +13.3% | no | B | 440,340–656,100 | 2 | — |
| Automotive Glass | heldout | 24,600 | 20,000–25,000 | 26,014 | +5.7% | yes | 27,711 | +12.6% | no | 22,880 | -7.0% | yes | B | 20,000–38,500 | 11 | — |
| Automotive Lubricants | heldout | 75,080 | 72,790–83,700 | 78,665 | +4.8% | yes | 178,140 | +137.3% | no | 86,430 | +15.1% | yes | A | 60,880–136,270 | 7 | — |
| Beer | heldout | 804,650 | 760,000–882,800 | 815,875 | +1.4% | yes | 815,875 | +1.4% | yes | 779,789 | -3.1% | yes | B | 662,879–896,700 | 2 | — |
| Bicycles | heldout | 84,250 | 71,100–95,800 | 68,000 | -19.3% | yes | 68,000 | -19.3% | yes | 81,960 | -2.7% | yes | A | 33,870–126,700 | 11 | — |
| Blood Glucose Monitoring Devices | heldout | 17,220 | 16,460–24,200 | 15,532 | -9.8% | no | 15,532 | -9.8% | no | 15,515 | -9.9% | no | B | 6,250–21,000 | 10 | — |
| Carbon Black | heldout | 21,600 | 18,500–28,860 | 24,665 | +14.2% | yes | 24,665 | +14.2% | yes | 24,570 | +13.8% | yes | A | 18,510–28,860 | 8 | low_confidence |
| Cardiac Pacemakers | heldout | 5,660 | 5,300–5,900 | 5,092 | -10.0% | yes | 5,092 | -10.0% | yes | 5,080 | -10.3% | yes | A | 2,040–6,701 | 10 | — |
| Caustic Soda | heldout | 46,500 | 46,440–50,150 | 47,600 | +2.4% | yes | 27,360 | -41.2% | no | 47,970 | +3.2% | yes | A | 30,458–54,653 | 8 | — |
| Chocolate | heldout | 167,000 | 129,600–171,600 | 129,600 | -22.4% | yes | 129,600 | -22.4% | yes | 129,100 | -22.7% | yes | A | 113,950–147,880 | 7 | — |
| Cosmetics | heldout | 346,840 | 339,000–354,680 | 361,858 | +4.3% | yes | 412,000 | +18.8% | no | 412,000 | +18.8% | no | A | 349,850–683,200 | 5 | — |
| Crop Protection Chemicals | heldout | 83,320 | 74,640–99,300 | 81,250 | -2.5% | yes | 50,300 | -39.6% | no | 70,730 | -15.1% | no | A | 50,300–83,320 | 5 | — |
| Data Center Colocation | heldout | 83,040 | 64,600–84,500 | 82,450 | -0.7% | yes | 82,450 | -0.7% | yes | 72,000 | -13.3% | yes | — | — | 0 | ungrounded_market_size, low_confidence |
| Drip Irrigation | heldout | 8,050 | 6,600–8,760 | 8,125 | +0.9% | yes | 6,285 | -21.9% | yes | 7,910 | -1.7% | yes | A | 7,200–18,600 | 11 | differs_from_cmi_report |
| Elevators | heldout | 98,720 | 87,900–100,230 | 90,780 | -8.0% | yes | 90,780 | -8.0% | yes | 105,000 | +6.4% | yes | — | — | 0 | ungrounded_market_size, low_confidence |
| Energy Drinks | heldout | 77,160 | 72,790–86,180 | 77,160 | +0.0% | yes | 77,160 | +0.0% | yes | 86,000 | +11.5% | yes | — | — | 0 | ungrounded_market_size, low_confidence |
| Flexible Packaging | heldout | 269,200 | 242,000–311,150 | 307,271 | +14.1% | yes | 307,271 | +14.1% | yes | 287,000 | +6.6% | yes | A | 266,115–314,541 | 4 | — |
| Forklifts | heldout | 78,780 | 62,850–81,400 | 80,605 | +2.3% | yes | 80,605 | +2.3% | yes | 48,000 | -39.1% | no | — | — | 0 | ungrounded_market_size, low_confidence |
| Fragrances | heldout | 60,000 | 58,140–72,720 | 57,600 | -4.0% | yes | 94,550 | +57.6% | no | 58,900 | -1.8% | yes | B | 30,100–60,200 | 3 | — |
| Hearing Aids | heldout | 8,800 | 8,640–15,110 | 11,876 | +35.0% | yes | 11,876 | +35.0% | yes | 9,270 | +5.3% | yes | A | 8,642–15,110 | 9 | — |
| Insulin | heldout | 29,200 | 21,130–30,760 | 29,404 | +0.7% | yes | 58,026 | +98.7% | no | 28,970 | -0.8% | yes | B | 19,300–69,540 | 6 | — |
| Olive Oil | heldout | 15,700 | 13,590–18,750 | 21,826 | +39.0% | no | 21,826 | +39.0% | no | 15,500 | -1.3% | yes | — | — | 0 | ungrounded_market_size |
| Paper Cups | heldout | 13,400 | 9,677–14,600 | 11,690 | -12.8% | yes | 11,690 | -12.8% | yes | 11,349 | -15.3% | yes | C | 11,349–11,349 | 1 | cited_values_disputed, low_confidence_citation |
| Potash Fertilizer | heldout | 29,560 | 22,600–32,270 | 23,560 | -20.3% | yes | 24,010 | -18.8% | yes | 25,122 | -15.0% | yes | A | 17,350–29,550 | 7 | — |
| Razors | heldout | 12,754 | 11,620–13,888 | 13,334 | +4.5% | yes | 13,334 | +4.5% | yes | 14,500 | +13.7% | yes | — | — | 0 | ungrounded_market_size, low_confidence |
| Semiconductors | heldout | 794,300 | 793,000–795,600 | 685,030 | -13.8% | no | 685,030 | -13.8% | no | 700,000 | -11.9% | no | — | — | 0 | ungrounded_market_size, low_confidence |
| Smart Meters | heldout | 32,550 | 28,830–32,900 | 29,078 | -10.7% | yes | 29,078 | -10.7% | yes | 32,000 | -1.7% | yes | — | — | 0 | ungrounded_market_size, low_confidence |
| Smart Speakers | heldout | 14,250 | 12,300–15,570 | 15,600 | +9.5% | yes | 15,600 | +9.5% | yes | 14,253 | +0.0% | yes | C | 14,253–14,253 | 1 | cited_values_disputed, low_confidence_citation |
| Smartphones | heldout | 585,630 | 537,620–598,920 | 582,044 | -0.6% | yes | 582,044 | -0.6% | yes | 520,000 | -11.2% | yes | — | — | 0 | ungrounded_market_size, low_confidence |
| Smartwatches | heldout | 38,000 | 32,010–38,530 | 32,010 | -15.8% | yes | 38,530 | +1.4% | yes | 40,965 | +7.8% | no | C | 17,540–76,170 | 6 | cited_values_disputed, low_confidence_citation |
| Surgical Masks | heldout | 5,060 | 4,140–5,900 | 6,755 | +33.5% | no | 6,755 | +33.5% | no | 4,730 | -6.5% | yes | A | 2,500–6,409 | 11 | — |
| Tablets | heldout | 64,000 | 55,100–92,700 | 72,700 | +13.6% | yes | 50,000 | -21.9% | no | 76,500 | +19.5% | yes | — | — | 0 | ungrounded_market_size, low_confidence |
| Televisions | heldout | 64,355 | 63,900–64,810 | 278,965 | +333.5% | no | 119,000 | +84.9% | no | 231,000 | +258.9% | no | — | — | 0 | ungrounded_market_size, low_confidence |
| Toothpaste | heldout | 22,300 | 20,140–29,320 | 23,500 | +5.4% | yes | 23,500 | +5.4% | yes | 18,000 | -19.3% | no | — | — | 0 | ungrounded_market_size, low_confidence |
| Toys | heldout | 121,750 | 120,500–123,000 | 129,960 | +6.7% | no | 129,960 | +6.7% | no | 110,000 | -9.7% | no | — | — | 0 | ungrounded_market_size, low_confidence |
| Urea (total, all uses) | heldout | 73,500 | 49,720–79,730 | 73,768 | +0.4% | yes | 73,768 | +0.4% | yes | 70,624 | -3.9% | yes | A | 64,200–82,760 | 4 | — |
| Wine | heldout | 360,360 | 358,500–452,907 | 424,040 | +17.7% | yes | 424,040 | +17.7% | yes | 499,775 | +38.7% | no | B | 380,000–549,640 | 4 | — |
| Wireless Earbuds | heldout | 35,600 | 31,900–39,300 | 78,500 | +120.5% | no | 78,500 | +120.5% | no | 52,300 | +46.9% | no | C | 27,500–78,500 | 3 | cited_values_disputed, low_confidence_citation |
| Air Compressors | tuning | 19,700 | 18,960–29,670 | 19,188 | -2.6% | yes | 46,921 | +138.2% | no | 23,193 | +17.7% | yes | B | 18,960–27,426 | 2 | — |
| Aluminum Cans | tuning | 60,330 | 53,700–63,200 | 58,771 | -2.6% | yes | 58,771 | -2.6% | yes | 60,586 | +0.4% | yes | B | 47,340–75,500 | 4 | — |
| Athletic Footwear | tuning | 145,490 | 130,100–147,500 | 137,085 | -5.8% | yes | 137,085 | -5.8% | yes | 129,175 | -11.2% | yes | B | 87,000–171,350 | 2 | — |
| Automotive Lead-Acid Batteries | tuning | 31,300 | 13,886–31,790 | 28,420 | -9.2% | yes | 28,420 | -9.2% | yes | 30,066 | -3.9% | yes | B | 19,735–51,660 | 8 | — |
| Bottled Water | tuning | 323,070 | 288,800–353,610 | 335,820 | +3.9% | yes | 335,820 | +3.9% | yes | 325,000 | +0.6% | yes | — | — | 0 | ungrounded_market_size, low_confidence |
| Cloud Computing | tuning | 781,270 | 723,400–912,770 | 897,100 | +14.8% | yes | 897,100 | +14.8% | yes | 907,440 | +16.1% | yes | A | 754,200–1,012,931 | 4 | — |
| Corrugated Boxes | tuning | 195,600 | 179,800–212,300 | 256,810 | +31.3% | no | 256,810 | +31.3% | no | 210,000 | +7.4% | yes | — | — | 0 | ungrounded_market_size, low_confidence |
| Cybersecurity | tuning | 227,600 | 218,980–248,000 | 233,825 | +2.7% | yes | 233,825 | +2.7% | yes | 210,000 | -7.7% | yes | — | — | 0 | ungrounded_market_size, low_confidence |
| Dental Implants | tuning | 5,640 | 5,200–7,100 | 6,767 | +20.0% | yes | 6,767 | +20.0% | yes | 5,552 | -1.6% | yes | A | 5,000–13,750 | 10 | — |
| Diesel Generators | tuning | 21,440 | 20,300–22,200 | 21,198 | -1.1% | yes | 21,198 | -1.1% | yes | 21,197 | -1.1% | yes | C | 21,197–21,197 | 1 | cited_values_disputed, low_confidence_citation |
| Digital Cameras | tuning | 22,285 | 20,120–24,450 | 9,516 | -57.3% | no | 9,516 | -57.3% | no | 8,065 | -63.8% | no | C | 7,870–8,260 | 2 | low_confidence_citation |
| LED Lighting | tuning | 108,990 | 56,626–109,110 | 95,000 | -12.8% | yes | 95,000 | -12.8% | yes | 87,720 | -19.5% | yes | A | 85,300–106,900 | 3 | — |
| Laptops | tuning | 115,100 | 114,000–132,100 | 163,806 | +42.3% | no | 134,900 | +17.2% | yes | 171,890 | +49.3% | no | A | 143,000–212,400 | 7 | — |
| Mattresses | tuning | 49,560 | 45,800–57,510 | 30,800 | -37.9% | no | 30,800 | -37.9% | no | 50,810 | +2.5% | yes | C | 49,240–52,380 | 2 | low_confidence_citation |
| Organic Fertilizers | tuning | 11,110 | 7,710–13,090 | 12,770 | +14.9% | yes | 12,985 | +16.9% | yes | 13,110 | +18.0% | yes | B | 7,710–18,500 | 10 | — |
| Orthopedic Implants | tuning | 52,400 | 49,730–58,200 | 39,325 | -25.0% | no | 51,650 | -1.4% | yes | 49,510 | -5.5% | yes | C | 21,945–58,200 | 11 | cited_values_disputed, low_confidence_citation |
| Paints & Coatings | tuning | 198,250 | 193,900–202,600 | 207,730 | +4.8% | yes | 207,730 | +4.8% | yes | 219,900 | +10.9% | no | A | 194,042–279,580 | 3 | differs_from_cmi_report |
| Plant-Based Milk | tuning | 22,660 | 21,350–25,100 | 22,130 | -2.3% | yes | 21,376 | -5.7% | yes | 22,515 | -0.6% | yes | A | 21,000–24,380 | 6 | — |
| Shampoo | tuning | 36,915 | 35,600–38,230 | 33,969 | -8.0% | yes | 33,969 | -8.0% | yes | 38,200 | +3.5% | yes | A | 32,480–38,420 | 7 | — |
| Sunglasses | tuning | 43,030 | 41,600–43,540 | 39,800 | -7.5% | yes | 39,800 | -7.5% | yes | 43,030 | +0.0% | yes | B | 43,000–43,580 | 3 | — |
| Syringes | tuning | 10,491 | 10,150–22,577 | 17,332 | +65.2% | yes | 17,332 | +65.2% | yes | 18,540 | +76.7% | yes | B | 10,150–26,850 | 10 | — |
| Titanium Dioxide | tuning | 21,615 | 21,230–22,000 | 22,250 | +2.9% | yes | 22,250 | +2.9% | yes | 21,126 | -2.3% | yes | A | 19,250–25,490 | 4 | — |
| Tractors | tuning | 84,670 | 80,930–88,410 | 89,245 | +5.4% | yes | 89,245 | +5.4% | yes | 83,873 | -0.9% | yes | A | 78,400–96,800 | 4 | — |
| Wheelchairs | tuning | 5,580 | 5,120–7,420 | 6,330 | +13.4% | yes | 7,424 | +33.0% | yes | 5,930 | +6.3% | yes | B | 3,900–10,950 | 11 | — |
| Wind Turbines | tuning | 170,900 | 161,990–171,000 | 107,090 | -37.3% | no | 107,090 | -37.3% | no | 95,000 | -44.4% | no | — | — | 0 | ungrounded_market_size, low_confidence |
