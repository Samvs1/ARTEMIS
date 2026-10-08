# Artemis: Shopping Lists

Two lists:

- **List A is the first prototype, "Desk Arty"**: a talking face on your desk. This is what to buy now.
- **List B is the final robot, "Arty"**: List A plus a body. It is a draft. Do not buy from it yet, because the prototype will teach us what to change.

The rule for now (decided in round 7): get the basics working first. That means a real face, a real voice and a real microphone on a real Raspberry Pi. Wheels, a battery, sensors and a dock come after that. The ears or antennae, the mood glow, the camera and the neck tilt are parked until the basics work.

**About the prices.** They come from shop listings and news posts I read in early October 2026. They are euro with VAT where the shop shows it, shipping not included, and they are not quotes. Raspberry Pi has raised its prices several times since December 2025 because of the memory shortage (most recently, in October, for the 2 GB boards), so older prices on the web are too low. A 4 GB Pi 5 far below 100 euro is old, a different board, or a seller to avoid. Check the shop page on the day you order.

## List A: first prototype, "Desk Arty" (buy now)

What it proves:

- Arty's face on the real screen.
- The whole talking loop on real hardware: wake word, listening, the mind (DeepSeek), the voice (Fish Audio), and no echo of Arty's own voice.
- The real delay between you finishing a sentence and Arty's first sound (the target is about one second).

| # | Part | What it is for | About | Need |
|---|------|----------------|-------|------|
| 1 | Raspberry Pi 5, 4 GB | The computer in Arty's head: runs the face, the audio and the speech software | €117 to 140 | Yes |
| 2 | Official 27 W USB-C power supply, EU plug | The Pi 5 wants 5 V and 5 A. A weaker charger makes it limit the power for USB devices, which the mic array uses | €14 to 15 | Yes |
| 3 | Active Cooler (small fan and heatsink for the Pi 5) | Stops the Pi slowing down when the browser and the speech software run together | €5 to 6 | Yes |
| 4 | microSD card, 64 GB, A2 speed class, known brand | The Pi's disk | €10 to 15 | Yes |
| 5 | microSD card reader for your Windows PC | To put the operating system on the card. Skip it if your PC has an SD slot | €0 to 8 | Maybe |
| 6 | Waveshare 4 inch capacitive touch display, 480 x 800, DSI cable connection | The face. This is the screen the final robot will use too | €25 to 69 | Yes |
| 7 | Display cable for the Pi 5 (22 pin to 15 pin, about 12 cm) | The Pi 5's display plug is smaller than on older Pis. Some shops include the right cable, some do not | €0 to 6 | Check |
| 8 | reSpeaker XVF3800 USB 4-microphone array, plain USB version | Four microphones with echo cancellation and direction finding. It hears you over Arty's own voice, so you can interrupt, and it knows roughly where you are | €47 to 58 | Yes |
| 9 | Small speaker with a 3.5 mm cable, powered over USB | Arty's voice. It plugs into the mic array's headphone jack, so the array knows what is playing and can cancel it | €10 to 15 | Yes |
| 10 | Micro-HDMI to HDMI cable | The fallback: if the DSI screen fights you, use any monitor or TV for setup | €5 to 10 | Optional |
| 11 | Small 4 or 8 ohm, 3 W speaker with a JST plug | The mic array also has a speaker plug (JST, up to 5 W). This is how a speaker would be wired inside the final robot. Check Seeed's wiki for the plug type and impedance before buying | €3 to 8 | Optional |

**Total for the core (rows 1 to 4, 6, 8, 9): about €228 to 318.** With every extra: about €240 to 350. Most of the spread is the Pi's price and where you buy the screen. For comparison, a "lite" version that uses a monitor you already own, a cheap USB microphone and any speaker would be about €170 to 225, but it throws away the parts that are the real thing and gives Arty no echo cancellation, so Arty could not be interrupted.

Notes on the rows:

1. **Why 4 GB.** The face is a web page in a browser and the speech software is small, so 4 GB should be comfortable (to be measured on the real Pi). 2 GB is only about €25 to 45 cheaper and would be tight. 8 GB costs about €50 to 70 more and is only needed for big on-device speech models. Memory cannot be upgraded later. A Pi 4 would not save much at today's prices (I estimate it at roughly €105, unverified) and is clearly slower. Seen: BerryBase (Germany) €116.50, [Tinytronics](https://www.tinytronics.nl/en/development-boards/single-board-computers/raspberry-pi-5-4gb) €132 (2 to 4 week lead time), JB-Computer and Pollin about €140, and [Kiwi Electronics](https://www.kiwi-electronics.com/en/brand-raspberry-pi/raspberry-pi-5-4gb-11579) lists it (no price seen).
6. **The display.** Its shop names are "4-DSI-TOUCH-A" and "4inch DSI LCD" (article number 21687). The cheapest price I saw is Waveshare's own shop at €25.22 (check shipping, VAT and customs at checkout). EU resellers ask about €53 to 69 ([Opencircuit](https://opencircuit.shop/product/4inch-capacitive-touch-display-raspberry-pi), [Grobotronics](https://grobotronics.com/waveshare-touch-ips-lcd-4-480800-dsi.html?sl=en), [Eckstein](https://eckstein-shop.de/WaveShare-4inch-Capacitive-Touch-Display-for-Raspberry-Pi-480x800-DSI-Interface-EN), [Openelab](https://openelab.io/de/products/waveshare-inch-dsi-capacitive-touch)). It is a portrait panel, so the software rotates it to landscape. Do not order the round 720 x 720 one. Its 4.3 inch sibling (800 x 480, landscape out of the box) would also work. Setup steps are on [Waveshare's wiki](https://waveshare.com/wiki/4inch_DSI_LCD).
8. **The mic array.** Buy the plain USB board (Seeed article 101991441), for example at [Botland](https://botland.store/microphones-and-sound-detection/28171-respeaker-xmos-xvf3800-4-microphone-usbi2s-array-for-ai-voice-recognition-seeedstudio-101991441-5904422352950.html) or Mouser (about €47). Do not buy the versions "with XIAO ESP32S3": they ship with different firmware and need a flashing step. It records at up to 16 kHz, which is fine for speech. Details are on [Seeed's wiki](https://wiki.seeedstudio.com/respeaker_xvf3800_introduction/).
9. **The speaker.** The array has two audio outputs: a 3.5 mm jack for headphones or powered speakers, and a JST plug for a 5 W speaker. A powered speaker on the jack is the safe choice. Sound has to leave through the array, not through the Pi's own jack, or the echo cancelling cannot work.
11. **The JST speaker** is an experiment. I could not confirm the impedance or the pin order from the pages I could read.

Before you pay:

- Pi 5 (not Pi 4), 4 GB, and the 27 W power supply with an EU plug.
- Display: 4 inch, 480 x 800, capacitive touch, DSI. Make sure a Pi 5 cable is in the box.
- Mic array: plain USB version, not the "XIAO ESP32S3" ones.
- Order from as few shops as possible, to save on shipping.

If something fights you:

- The DSI screen: use any monitor with the micro-HDMI cable (row 10) while we sort it out.
- The mic array: use any USB microphone, with the microphone muted while Arty talks. It works, but Arty cannot be interrupted.

When the box arrives, in order:

1. Put Raspberry Pi OS (64-bit, with desktop) on the card with Raspberry Pi Imager on your PC, and set the Wi-Fi and a username there.
2. Attach the screen and get a picture, rotated to landscape.
3. Start the mind server and open the face in a full-screen browser.
4. Plug in the mic array and the speaker, and record and play a test sound.
5. Add the Python process that listens (wake word, speech to text) and speaks.

### Where to buy in the Netherlands or Belgium

A quick look, not a full price comparison. Dutch and Belgian VAT are both 21%, and several shops show prices without VAT. All of this must be checked on the day you order.

- **Kiwi Electronics (NL)**, the likely one-stop shop. Seen without VAT: Pi 5 4 GB €98.99 (about €120 with VAT, stock unclear), 27 W power supply €10.89 (about €13.20), Active Cooler €4.49 (about €5.45), XVF3800 plain USB array €44.99 (about €54.40). Their microSD cards and cables are not looked at yet. Kiwi sells other Waveshare displays, but I did not find the 4 inch 480 x 800 one there.
- **The display** is the hard one to place. [Elektronicavoorjou](https://elektronicavoorjou.nl/en/product/4-inch-480x800-LCD-display-touch-screen/) (NL) had it at €52.95 with VAT, with conflicting stock information. Waveshare's own shop is cheaper (about €25 to 29) but ships from abroad. Ask Kiwi or Tinytronics if they can get it.
- **The display cable.** Waveshare sells 22-pin to 15-pin cables for the Pi 5 in 200, 300 and 500 mm for a few euro. A 12 cm to 20 cm one suits the desk rig. Check which cable is in the box before buying one.
- **Other shops seen:** Tinytronics (NL; Pi 5 4 GB €132 with a long lead time, 27 W power supply €13.50), Mouser Belgium (XVF3800 for €47.21, VAT status unclear), Opencircuit and OpenELAB.

## List B: final robot, "Arty" (draft, do not buy yet)

Arty = everything in List A, plus the parts below. Almost all of List A is reused. Nothing in List A is wasted.

### Stage 2: the body (Arty rolls)

| Group | Part | About |
|-------|------|-------|
| Drive | 2 gearmotors with encoders (small N20 type for a light robot, about €6 to 8 each; 25 mm type about €10 to 20 each for a heavier robot or for door thresholds and carpet), 2 wheels of about 65 mm with mounts, a dual motor driver (TB6612FNG type, about €5 to 8), a printed rear skid | €25 to 65 |
| Real-time controller | Raspberry Pi Pico 2 (about €5) handles the motors and sensors and talks to the Pi over USB | €5 |
| Sensors | 2 or 3 small time-of-flight distance sensors for cliffs and the front (VL53L0X or VL53L1X, about €4 to 11 each) and a motion sensor (LSM6DSO type, about €4 to 15) for "picked up", "tipped" and "bumped" | €15 to 50 |
| Power | A battery board for the Pi with room for four 21700 cells (Waveshare UPS HAT (E), about €35 to 50) plus four cells (about €25 to 40), a separate small supply for the motors, a main switch, a fuse and wiring | €75 to 115 |
| Privacy | A slide switch that cuts the microphone's power, so the mic-kill promise is real | €2 to 5 |
| Body | 3D printing at a print service (or only filament at a makerspace), screws, standoffs and heat-set inserts | €40 to 85 |
| Wiring | Connectors, cables, a small prototype board | €10 to 20 |

**Stage 2 adds about €170 to 345 on top of List A.** Together that is about €400 to 665. The middle of those ranges is about €530, which is a little above the original 250 to 500 budget. The ways to trim are: use a makerspace for printing, use small N20 motors if Arty stays under about 600 g on hard floors, skip the dock at first and charge by hand, and leave the camera out.

Open choices that change this list:

- **Weight and floors.** With a Pi 5, a screen and four cells Arty may weigh 0.8 to 1 kg. Small N20 motors would struggle on carpet or over a door threshold, so the motors are chosen after we weigh the prototype.
- **Chassis.** Print our own, or start from a kit. Kits seen: Pololu Romi €35.86 (70 mm wheels, no encoders in the box, via [Voelkner](https://www.voelkner.de/products/12152010/Pololu-Romi-Chassis-Kit-Black.html)) and DFRobot Turtle €42.24 (65 mm wheels, encoders not listed, via [Voelkner](https://www.voelkner.de/products/12151788/DFRobot-Turtle-2WD-Mobile-Roboter-Plattform-fuer-Arduino.html)). I found no kit near €40 that includes encoders.
- **Battery.** The board above gives a battery gauge over I2C (Arty can know it is tired) and shuts the Pi down safely. Lithium cells are the one part of this project that can hurt: buy named-brand cells from a reputable seller and we will review the battery plan together before you order. Prices seen: [Waveshare UPS HAT (E)](https://www.waveshare.com/ups-hat-e.htm) $32.99, [The Pi Hut](https://thepihut.com/products/21700-ups-hat-e-for-raspberry-pi-5-4-3) £31.70. I found no euro price. The alternative is Geekworm's [X1202](https://geekworm.com/collections/raspberry-pi/products/x1202) for four 18650 cells (about $49).
- **Screen.** Keep the 4 inch panel, or move to a bigger or round one once we know the head size.

### Stage 3: comfort and polish

| Part | About |
|------|-------|
| Charging dock: printed ramp, charging contacts, charger module | €20 to 35 |
| A better speaker in a printed sound chamber | €10 to 20 |
| Touch sensor for head pats, ambient light sensor, status light | €5 to 15 |

### Parked (decided in round 7)

Not needed at this stage, and not in the totals above:

- Ears or antennae (two small servos, about €6 to 10) and the mood glow (LED ring, about €5 to 10).
- Camera Module 3 (about €34 to 45) with a shutter, and a neck tilt servo.
- Arms or a lift.

The simulator still draws the ears and the glow. That is design exploration, not a promise.

## Sources

Prices and facts above come from these pages (seen early October 2026; they may have changed):

- Pi 5 prices and the 2026 increases: [OMG Ubuntu](https://www.omgubuntu.co.uk/2026/10/raspberry-pi-2gb-costs-how-much-now/amp), [TechSpot](https://www.techspot.com/news/111165-raspberry-pi-prices-soar-amid-ai-memory-shortage.html), the shop pages linked in the notes.
- Display: [Waveshare 4inch DSI LCD](https://waveshare.com/4inch-DSI-LCD.htm), its [wiki](https://waveshare.com/wiki/4inch_DSI_LCD), and the reseller pages in the notes.
- Mic array: [Seeed wiki](https://wiki.seeedstudio.com/respeaker_xvf3800_introduction/), [Botland](https://botland.store/microphones-and-sound-detection/28171-respeaker-xmos-xvf3800-4-microphone-usbi2s-array-for-ai-voice-recognition-seeedstudio-101991441-5904422352950.html).
- Battery boards: [Waveshare](https://www.waveshare.com/ups-hat-e.htm), [The Pi Hut](https://thepihut.com/products/21700-ups-hat-e-for-raspberry-pi-5-4-3), [Electrokit](https://www.electrokit.com/en/ups-stromforsorjning-4x-21700-celler-for-raspberry-pi), [Geekworm](https://geekworm.com/collections/raspberry-pi/products/x1202).
- Pico 2: [Olimex](https://www.olimex.com/Products/RaspberryPi/RP2350-PICO2) €5.00, [TME](https://www.tme.eu/gb/details/sc1631/raspberry-pi-embedded/raspberry-pi/raspberry-pi-pico-2/) about €5.
- Motion sensor: [Soldered LSM6DSO](https://soldered.com/products/accelerometer-gyroscope-lsm6dso-6-dof-breakout) (€3.95 on sale, normally €9.95). Distance sensors: [Electrokit VL53L1X](https://www.electrokit.com/en/distance-sensor-tof-lidar-30-4000mm-vl53l1x-qwiic-1) and generic modules on eBay for €3.50 to 11 delivered.
- Items I estimated without a shop page: microSD card, card reader, USB speaker, cables, printing, cells, wiring.
