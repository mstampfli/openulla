# FCC exhibits

Index of the public FCC certification exhibits used for the AirPods Pro 2
USB-C left earbud (FCC ID BCG-A3047) and the Apple Vision Pro (FCC ID
BCGA2117): the reports about the Narrowband UNII radio and the 2.4 GHz
Bluetooth HDR modes. The exhibits themselves are not redistributed in this
repository. They are at https://fccid.io/BCG-A3047 and
https://fccid.io/BCGA2117; `tools/fetch_fcc.sh` downloads them and extracts
their text with `pdftotext -layout`, producing the file names below.

| File | Device | Content |
|---|---|---|
| BCG-A3047_NB-UNII-BDR-Test-Report-6764877.txt | AirPods | 5 GHz GFSK 1 Mbit/s mode, 2023-09-12 |
| BCG-A3047_NB-UNII-HDR-Test-Report-6764878.txt | AirPods | 5 GHz HDR4 and HDR8, hopping statement, device capabilities |
| BCG-A3047_NB-UNII-LE-Test-Report-6764879.txt | AirPods | 5 GHz LE 2M mode |
| BCG-A3047_NB-UNII-5-Test-Report-1-7595478.txt | AirPods | 6 GHz VLP, 2024-08-30 |
| BCG-A3047_NB-UNII-5-Test-Report-2-7595481.txt | AirPods | 6 GHz VLP, link setup with Vision Pro as companion, TPC |
| BCG-A3047_Bluetooth-HDR-Test-Report-6764849.txt | AirPods | HDR4 and HDR8 in the 2.4 GHz Bluetooth band |
| BCGA2117_NB-UNII-BDR-Test-Report-6962411.txt | Vision Pro | 5 GHz GFSK mode, device capabilities |
| BCGA2117_NB-UNII-HDR-Test-Report-6962412.txt | Vision Pro | 5 GHz HDR4 and HDR8 |
| BCGA2117_NB-UNII-LE-Test-Report-6962414.txt | Vision Pro | 5 GHz LE mode |
| BCGA2117_NB-UNII-5-Test-Report-1-7587864.txt | Vision Pro | 6 GHz VLP |
| BCGA2117_NB-UNII-5-Test-Report-2-7587866.txt | Vision Pro | 6 GHz VLP, emission mask method |
| BCGA2117_NB-UNII-5-Test-Report-3-7587867.txt | Vision Pro | 6 GHz VLP, radiated |
