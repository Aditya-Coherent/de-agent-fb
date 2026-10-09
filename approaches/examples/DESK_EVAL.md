# Desk Research vs actual — all 'ok' markets (+ Urea total), three code versions

% error vs the median of published values; in range = within [min, max] published ±5%. Tuning/held-out split fixed before any tuning (`DESK_EVAL_SPLIT.json`, hash of the market name). Web search results differ run to run, so part of every difference is search noise.

## Held-out (38 markets; 26 with a value in all three versions)

| Version | Median abs error, all with a value | Median abs error, common markets |
|---|---|---|
| old anchor (HEAD) | 17.6% (n=27), in range 12/27 | 18.9% (n=26), in range 11/26 |
| current code | 8.3% (n=26), in range 19/26 | 8.3% (n=26), in range 19/26 |
| after this change | 13.9% (n=38), in range 23/38 | 14.2% (n=26), in range 17/26 |
| after, shipped defaults (weak_consensus off) | 8.8% (n=38), in range 31/38 | 6.9% (n=26), in range 22/26 |

Ablation (after-code evidence replayed offline, one step flipped from the shipped defaults; common markets):

| Step flipped | Median abs error | Shipped defaults on the same markets |
|---|---|---|
| title_subject off | 9.8% (n=25) | 5.7% |
| size_check off | 6.9% (n=26) | 6.9% |
| source_tiers off | 8.2% (n=26) | 6.9% |
| weak_consensus on | 14.2% (n=26) | 6.9% |

## Tuning (25 markets; 16 with a value in all three versions)

| Version | Median abs error, all with a value | Median abs error, common markets |
|---|---|---|
| old anchor (HEAD) | 25.7% (n=16), in range 8/16 | 25.7% (n=16), in range 8/16 |
| current code | 5.6% (n=16), in range 13/16 | 5.6% (n=16), in range 13/16 |
| after this change | 9.2% (n=25), in range 20/25 | 6.6% (n=16), in range 16/16 |
| after, shipped defaults (weak_consensus off) | 9.2% (n=25), in range 19/25 | 6.6% (n=16), in range 16/16 |

Ablation (after-code evidence replayed offline, one step flipped from the shipped defaults; common markets):

| Step flipped | Median abs error | Shipped defaults on the same markets |
|---|---|---|
| title_subject off | 5.6% (n=16) | 6.6% |
| size_check off | 6.6% (n=16) | 6.6% |
| source_tiers off | 6.5% (n=16) | 6.6% |
| weak_consensus on | 6.6% (n=16) | 6.6% |

## All (63 markets; 42 with a value in all three versions)

| Version | Median abs error, all with a value | Median abs error, common markets |
|---|---|---|
| old anchor (HEAD) | 20.1% (n=43), in range 20/43 | 20.9% (n=42), in range 19/42 |
| current code | 7.3% (n=42), in range 32/42 | 7.3% (n=42), in range 32/42 |
| after this change | 13.0% (n=63), in range 43/63 | 12.7% (n=42), in range 33/42 |
| after, shipped defaults (weak_consensus off) | 9.2% (n=63), in range 50/63 | 6.6% (n=42), in range 38/42 |

## Every market

| Market | Set | Actual | Before | Err | In range | Current | Err | In range | After (as run) | Err | In range | After (shipped) | Err | In range | Grounded | After method | After flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Animal Feed | heldout | 483,690 | 45,000 | -90.7% | no | 483,880 | +0.0% | yes | 420,820 | -13.0% | yes | 510,000 | +5.4% | yes | yes | disputed: value closest to units x ASP mid $387,298M (https://www.fortunebusinessinsights.com/animal-feed-market-113128) | cited_values_disputed |
| Automotive Glass | heldout | 24,600 | 38,500 | +56.5% | no | 24,012 | -2.4% | yes | 27,711 | +12.6% | no | 26,014 | +5.7% | yes | yes | disputed: value closest to units x ASP mid $29,394M (https://www.psmarketresearch.com/market-analysis/automotive-glass-market) | cited_values_disputed |
| Automotive Lubricants | heldout | 75,080 | no DB skeleton | — | — | no DB skeleton | — | — | 178,140 | +137.3% | no | 78,665 | +4.8% | yes | yes | disputed: value closest to units x ASP mid $189,737M (https://www.marketsandmarkets.com/Market-Reports/lubricants-market-182046896.html) | cited_values_disputed |
| Beer | heldout | 804,650 | 663,000 | -17.6% | no | 882,800 | +9.7% | yes | 815,875 | +1.4% | yes | 815,875 | +1.4% | yes | yes | median of 4 cited values | — |
| Bicycles | heldout | 84,250 | no DB skeleton | — | — | no DB skeleton | — | — | 68,000 | -19.3% | yes | 68,000 | -19.3% | yes | no | none | ungrounded_market_size, low_confidence |
| Blood Glucose Monitoring Devices | heldout | 17,220 | 15,500 | -10.0% | no | 15,566 | -9.6% | no | 15,532 | -9.8% | no | 15,532 | -9.8% | no | yes | median of 2 cited values | — |
| Carbon Black | heldout | 21,600 | 20,500 | -5.1% | yes | 22,221 | +2.9% | yes | 24,665 | +14.2% | yes | 24,665 | +14.2% | yes | yes | median of 6 cited values | low_confidence |
| Cardiac Pacemakers | heldout | 5,660 | no DB skeleton | — | — | no DB skeleton | — | — | 5,092 | -10.0% | yes | 5,092 | -10.0% | yes | yes | median of 4 cited values | — |
| Caustic Soda | heldout | 46,500 | 78,080 | +67.9% | no | 46,500 | +0.0% | yes | 27,360 | -41.2% | no | 47,600 | +2.4% | yes | yes | disputed: value closest to units x ASP mid $33,675M (https://www.globalgrowthinsights.com/market-reports/caustic-soda-market-114653) | cited_values_disputed |
| Chocolate | heldout | 167,000 | 150,000 | -10.2% | yes | 130,000 | -22.2% | yes | 129,600 | -22.4% | yes | 129,600 | -22.4% | yes | yes | median of 7 cited values | — |
| Cosmetics | heldout | 346,840 | 450,000 | +29.7% | no | 416,545 | +20.1% | no | 412,000 | +18.8% | no | 361,858 | +4.3% | yes | yes | disputed: value closest to units x ASP mid $387,298M (https://www.emergenresearch.com/industry-report/cosmetics-market) | cited_values_disputed |
| Crop Protection Chemicals | heldout | 83,320 | 83,320 | +0.0% | yes | 82,180 | -1.4% | yes | 50,300 | -39.6% | no | 81,250 | -2.5% | yes | yes | disputed: value closest to units x ASP mid $28,284M (https://www.grandviewresearch.com/industry-analysis/crop-protection-chemicals-market) | cited_values_disputed |
| Data Center Colocation | heldout | 83,040 | 275,000 | +231.2% | no | 89,815 | +8.2% | no | 82,450 | -0.7% | yes | 82,450 | -0.7% | yes | yes | median of 4 cited values | — |
| Drip Irrigation | heldout | 8,050 | 6,000 | -25.5% | no | 8,450 | +5.0% | yes | 6,285 | -21.9% | yes | 8,125 | +0.9% | yes | yes | disputed: value closest to units x ASP mid $2,236M (CMI report DB: Drip Irrigation Market) | cited_values_disputed |
| Elevators | heldout | 98,720 | 101,580 | +2.9% | yes | 91,400 | -7.4% | yes | 90,780 | -8.0% | yes | 90,780 | -8.0% | yes | yes | median of 3 cited values | — |
| Energy Drinks | heldout | 77,160 | 20,000 | -74.1% | no | 82,695 | +7.2% | yes | 77,160 | +0.0% | yes | 77,160 | +0.0% | yes | yes | median of 3 cited values | — |
| Flexible Packaging | heldout | 269,200 | 300,000 | +11.4% | yes | 314,541 | +16.8% | yes | 307,271 | +14.1% | yes | 307,271 | +14.1% | yes | yes | median of 2 cited values | — |
| Forklifts | heldout | 78,780 | no DB skeleton | — | — | no DB skeleton | — | — | 80,605 | +2.3% | yes | 80,605 | +2.3% | yes | yes | median of 4 cited values | — |
| Fragrances | heldout | 60,000 | 34,000 | -43.3% | no | 35,260 | -41.2% | no | 94,550 | +57.6% | no | 57,600 | -4.0% | yes | yes | disputed: value closest to units x ASP mid $97,980M (https://www.skyquestt.com/report/fragrance-market) | cited_values_disputed |
| Hearing Aids | heldout | 8,800 | 8,642 | -1.8% | yes | 10,054 | +14.3% | yes | 11,876 | +35.0% | yes | 11,876 | +35.0% | yes | yes | median of 2 cited values | — |
| Insulin | heldout | 29,200 | no DB skeleton | — | — | no DB skeleton | — | — | 58,026 | +98.7% | no | 29,404 | +0.7% | yes | yes | disputed: value closest to units x ASP mid $47,434M (https://www.fnfresearch.com/news/global-insulin-market-value-projected-over-629-billion) | cited_values_disputed |
| Olive Oil | heldout | 15,700 | 15,500 | -1.3% | yes | 17,553 | +11.8% | yes | 21,826 | +39.0% | no | 21,826 | +39.0% | no | yes | median of 4 cited values | — |
| Paper Cups | heldout | 13,400 | 11,305 | -15.6% | yes | 12,185 | -9.1% | yes | 11,690 | -12.8% | yes | 11,690 | -12.8% | yes | yes | median of 5 cited values | — |
| Potash Fertilizer | heldout | 29,560 | 28,500 | -3.6% | yes | 27,566 | -6.7% | yes | 24,010 | -18.8% | yes | 23,560 | -20.3% | yes | yes | disputed: value closest to units x ASP mid $23,717M (https://www.databridgemarketresearch.com/reports/global-potash-fertilizers-market) | cited_values_disputed |
| Razors | heldout | 12,754 | 22,000 | +72.5% | no | 13,606 | +6.7% | yes | 13,334 | +4.5% | yes | 13,334 | +4.5% | yes | yes | median of 8 cited values | — |
| Semiconductors | heldout | 794,300 | no DB skeleton | — | — | no DB skeleton | — | — | 685,030 | -13.8% | no | 685,030 | -13.8% | no | yes | median of 2 cited values | — |
| Smart Meters | heldout | 32,550 | no DB skeleton | — | — | no DB skeleton | — | — | 29,078 | -10.7% | yes | 29,078 | -10.7% | yes | yes | median of 3 cited values | — |
| Smart Speakers | heldout | 14,250 | 14,250 | +0.0% | yes | failed | — | — | 15,600 | +9.5% | yes | 15,600 | +9.5% | yes | yes | median of 7 cited values | — |
| Smartphones | heldout | 585,630 | no DB skeleton | — | — | no DB skeleton | — | — | 582,044 | -0.6% | yes | 582,044 | -0.6% | yes | yes | median of 9 cited values | — |
| Smartwatches | heldout | 38,000 | 30,350 | -20.1% | no | 58,087 | +52.9% | no | 38,530 | +1.4% | yes | 32,010 | -15.8% | yes | yes | disputed: value closest to units x ASP mid $35,355M (https://www.fortunebusinessinsights.com/smartwatch-market-106625) | cited_values_disputed |
| Surgical Masks | heldout | 5,060 | 3,240 | -36.0% | no | 4,845 | -4.2% | yes | 6,755 | +33.5% | no | 6,755 | +33.5% | no | yes | median of 2 cited values | — |
| Tablets | heldout | 64,000 | no DB skeleton | — | — | no DB skeleton | — | — | 50,000 | -21.9% | no | 72,700 | +13.6% | yes | yes | disputed: value closest to units x ASP mid $49,295M (https://www.futuremarketinsights.com/reports/tablet-market) | cited_values_disputed |
| Televisions | heldout | 64,355 | 200,000 | +210.8% | no | 81,275 | +26.3% | no | 119,000 | +84.9% | no | 278,965 | +333.5% | no | yes | disputed: value closest to units x ASP mid $103,923M (https://www.marketresearchintellect.com/product/televisions-market/) | cited_values_disputed |
| Toothpaste | heldout | 22,300 | 19,380 | -13.1% | yes | 22,800 | +2.2% | yes | 23,500 | +5.4% | yes | 23,500 | +5.4% | yes | yes | median of 11 cited values | — |
| Toys | heldout | 121,750 | no DB skeleton | — | — | no DB skeleton | — | — | 129,960 | +6.7% | no | 129,960 | +6.7% | no | yes | median of 2 cited values | — |
| Urea (total, all uses) | heldout | 73,500 | 67,748 | -7.8% | yes | 79,730 | +8.5% | yes | 73,768 | +0.4% | yes | 73,768 | +0.4% | yes | yes | median of 2 cited values | — |
| Wine | heldout | 360,360 | 544,750 | +51.2% | no | 487,721 | +35.3% | no | 424,040 | +17.7% | yes | 424,040 | +17.7% | yes | yes | median of 2 cited values | — |
| Wireless Earbuds | heldout | 35,600 | no DB skeleton | — | — | no DB skeleton | — | — | 78,500 | +120.5% | no | 78,500 | +120.5% | no | yes | single cited value | cited_values_disputed |
| Air Compressors | tuning | 19,700 | no DB skeleton | — | — | no DB skeleton | — | — | 46,921 | +138.2% | no | 19,188 | -2.6% | yes | yes | disputed: value closest to units x ASP mid $77,460M (https://www.linkedin.com/pulse/air-compressor-market-size-2026-digital-ai-impact-trends-gmlgc/) | cited_values_disputed |
| Aluminum Cans | tuning | 60,330 | 56,390 | -6.5% | yes | 55,116 | -8.6% | yes | 58,771 | -2.6% | yes | 58,771 | -2.6% | yes | yes | median of 2 cited values | — |
| Athletic Footwear | tuning | 145,490 | 70,000 | -51.9% | no | 117,660 | -19.1% | no | 137,085 | -5.8% | yes | 137,085 | -5.8% | yes | yes | median of 6 cited values | — |
| Automotive Lead-Acid Batteries | tuning | 31,300 | 32,000 | +2.2% | yes | 32,940 | +5.2% | yes | 28,420 | -9.2% | yes | 28,420 | -9.2% | yes | yes | single cited value | cited_values_disputed |
| Bottled Water | tuning | 323,070 | no DB skeleton | — | — | no DB skeleton | — | — | 335,820 | +3.9% | yes | 335,820 | +3.9% | yes | yes | median of 2 cited values | — |
| Cloud Computing | tuning | 781,270 | 1,013,000 | +29.7% | no | 1,012,931 | +29.7% | no | 897,100 | +14.8% | yes | 897,100 | +14.8% | yes | yes | median of 2 cited values | — |
| Corrugated Boxes | tuning | 195,600 | no DB skeleton | — | — | no DB skeleton | — | — | 256,810 | +31.3% | no | 256,810 | +31.3% | no | yes | median of 3 cited values | — |
| Cybersecurity | tuning | 227,600 | 18,000 | -92.1% | no | 195,000 | -14.3% | no | 233,825 | +2.7% | yes | 233,825 | +2.7% | yes | yes | median of 2 cited values | — |
| Dental Implants | tuning | 5,640 | 5,850 | +3.7% | yes | 5,544 | -1.7% | yes | 6,767 | +20.0% | yes | 6,767 | +20.0% | yes | yes | median of 2 cited values | — |
| Diesel Generators | tuning | 21,440 | 21,117 | -1.5% | yes | 21,320 | -0.6% | yes | 21,198 | -1.1% | yes | 21,198 | -1.1% | yes | yes | median of 4 cited values | — |
| Digital Cameras | tuning | 22,285 | no DB skeleton | — | — | no DB skeleton | — | — | 9,516 | -57.3% | no | 9,516 | -57.3% | no | yes | median of 2 cited values | — |
| LED Lighting | tuning | 108,990 | 95 | -99.9% | no | 97,705 | -10.4% | yes | 95,000 | -12.8% | yes | 95,000 | -12.8% | yes | no | none | ungrounded_market_size, low_confidence |
| Laptops | tuning | 115,100 | no DB skeleton | — | — | no DB skeleton | — | — | 134,900 | +17.2% | yes | 163,806 | +42.3% | no | yes | disputed: value closest to units x ASP mid $134,164M (https://www.maximizemarketresearch.com/market-report/global-laptops-market/21050/) | cited_values_disputed |
| Mattresses | tuning | 49,560 | no DB skeleton | — | — | no DB skeleton | — | — | 30,800 | -37.9% | no | 30,800 | -37.9% | no | yes | median of 3 cited values | — |
| Organic Fertilizers | tuning | 11,110 | no DB skeleton | — | — | no DB skeleton | — | — | 12,985 | +16.9% | yes | 12,770 | +14.9% | yes | yes | median of 6 cited values | — |
| Orthopedic Implants | tuning | 52,400 | no DB skeleton | — | — | no DB skeleton | — | — | 51,650 | -1.4% | yes | 39,325 | -25.0% | no | yes | disputed: value closest to units x ASP mid $43,301M (https://www.skyquestt.com/report/orthopedic-implant-market) | cited_values_disputed |
| Paints & Coatings | tuning | 198,250 | 155,250 | -21.7% | no | 200,110 | +0.9% | yes | 207,730 | +4.8% | yes | 207,730 | +4.8% | yes | yes | median of 4 cited values | — |
| Plant-Based Milk | tuning | 22,660 | 23,100 | +1.9% | yes | 21,310 | -6.0% | yes | 21,376 | -5.7% | yes | 22,130 | -2.3% | yes | yes | disputed: value closest to units x ASP mid $21,213M (CMI report DB: Plant-Based Milk Market) | cited_values_disputed |
| Shampoo | tuning | 36,915 | 13,500 | -63.4% | no | 36,087 | -2.2% | yes | 33,969 | -8.0% | yes | 33,969 | -8.0% | yes | yes | median of 2 cited values | — |
| Sunglasses | tuning | 43,030 | 12,400 | -71.2% | no | 41,927 | -2.6% | yes | 39,800 | -7.5% | yes | 39,800 | -7.5% | yes | yes | median of 5 cited values | — |
| Syringes | tuning | 10,491 | 15,200 | +44.9% | yes | 19,450 | +85.4% | yes | 17,332 | +65.2% | yes | 17,332 | +65.2% | yes | yes | median of 6 cited values | — |
| Titanium Dioxide | tuning | 21,615 | 21,500 | -0.5% | yes | 22,500 | +4.1% | yes | 22,250 | +2.9% | yes | 22,250 | +2.9% | yes | yes | median of 8 cited values | — |
| Tractors | tuning | 84,670 | 89,245 | +5.4% | yes | 82,600 | -2.4% | yes | 89,245 | +5.4% | yes | 89,245 | +5.4% | yes | yes | median of 5 cited values | — |
| Wheelchairs | tuning | 5,580 | 11,800 | +111.5% | no | 5,960 | +6.8% | yes | 7,424 | +33.0% | yes | 6,330 | +13.4% | yes | yes | disputed: value closest to units x ASP mid $6,708M (https://www.coherentmarketinsights.com/market-insight/wheelchairs-market-457) | cited_values_disputed |
| Wind Turbines | tuning | 170,900 | no DB skeleton | — | — | no DB skeleton | — | — | 107,090 | -37.3% | no | 107,090 | -37.3% | no | yes | median of 2 cited values | — |
