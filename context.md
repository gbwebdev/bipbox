# Bipbox

## Intro

I designed a device/game for my son and grandson. I called it `bipbox`.

### Features
* **Telex**: One can go to a secured web-page and send a text message and/or a drawing/picture to the child. It is printed on a thermal printer.
* **Telegraphy**: On each device there is a LED and a pushbutton. When one child push the button on his box, it lights-up the LED on the other child’s.
* **VoIP**: Each box comes with a shoulder speakermic (with a PTT button). When one child press PTT and talk in his mic, his voice is played through the other child’s speaker.

### Hardware
In each box, there is:
* One MeanWell RS15-5 power supply
* One raspberry-pi zero w (2017)
* One Hub 4 USB from ZERO4U (specs in the appendices)
* One external USB sound card from UGREEN (specs in the appendices)
* One homemade HAT (see details after)
* One ESC/POS thermal printer (either a PRP-250 which is seen as a serial-over-usb device (`/dev/ttyACM*`) or a `Epson TM-T20II` (seen as a USB device))

## Hardware Specs

### I/O

On the top panel, there are 5 areas:
* System:
  * Power button (a simple pushbutton to power-up the Pi after a soft shutdown, and to trigger a soft shutdown)
  * Raspberry-pi activity LED (3mm low current green)
  * WiFi status LED (5mm blue)
* Power:
  * Power status LED (5mm red)
* Telex:
  * Telex status and activity (5mm green)
* VOIP:
  * 3.5mm TRRS jack for the shoulder speakermic
  * VOIP status and activity LED (5mm green)
* Telegraphy:
  * Incoming LED (5mm orange)
  * Pushbutton

On the back panel:
* C14 socket with integrated switch	and fusebox


### Homemade HAT

You can find the schematics, PCB, etc. in [./CAD/KiCAD](./CAD/KiCAD).

* Power:
  The hat distributes power from the `RS15-5` to:
  * The Pi
  * The 74HC595N that drives the LEDs (more on that later)
  * The USB hub (through its JST connector)
  I placed capacitors here and there on the power lines.
* LEDs:
  * The 74HC595N (SRCLK on Pi’s SPIO.SCLK/GPIO11, SER on SPIO.MOSI/GPIO10, RCLK on SPIO.CE1/SPIO7, SRCLR on 5V and OE on ground) drives:
    * The telegraphy LED (through a 180 ohm resistor, QC)
    * The telex green LED (through a 300 ohm resistor, QD)
    * The VoIP green LED (through a 300 ohm resistor, QE)
    * The Wi-Fi blue LED (through a 180 ohm resistor, QF)
  * The power LED is directly connected to the 5V power line through a 180 ohm resistor.
  * The low-current green LED is connected to GPIO26 through a 1k resistor
* Pushbuttons:
  * Power: directly between GPIO3 and ground
  * Telegraphy: between GPIO2 and ground, with a debounce circuit (10k resistor, 1k resistor and 2.2uF capacitor to ground in between)
  * PTT: to GPIO23. There are two variations of the wiring depending on whether the speakermic has a ring dedicated to the PPT switch on the TRRS or if we have to detect a voltage on the mic’s ring. More on that below.
* Audio:
  * The hat takes signals from the speakermic’s TRRS jack and routes it to two TRS jacks that plugs in the sound card.
    The board has two possible wirings (using jumpers, and by soldering or not some components):
    * Wiring 1 (for speakermics with a ring dedicated to PTT):
      * Tip: to the sound card’s speaker jack’s tip (through a 22ohm 1W resistor - possibility to solder a 47ohm resistor in parallel to lower the signal) - (sleeve is grounded)
      * Ring 1: To the sound card’s mic jack’s tip (sleeve is grounded)
      * Ring 2: To GPIO23
      * Sleeve: to ground
    * Wiring 2 (for speakermics with a shared mic/PTT):
      * Tip: to the sound card’s speaker jack’s tip (through a 22ohm 1W resistor - possibility to solder a 47ohm resistor in parallel to lower the signal) - (sleeve is grounded)
      * Ring 1: not wired
      * Ring 2: To the sound card’s mic jack’s tip (sleeve is grounded) through a 1uF capacitor.
        Also, we have a 10k resistor going to both a 100nF capa and a 2N700’s gate. The 2N7000’s source (and the capa’s other pin) are grounded and the drain goes to GPIO23
      * Sleeve: to ground

## Requirements

### I/O

* LEDs:
  * The low-current green led should reflect the pi’s activity (like its embedded LED)
  * The blue LED (74HC595N’s QF) should:
    * Be off when wifi is down
    * Blink slowly when looking for Wi-Fi
    * Blink in a `. . _ . . _ . .`… pattern (short, short, long, … repeated) when in AP mode (more on that later)
    * Blink fast when connecting to Wi-Fi
    * Be on (steady) when connected to Wi-Fi
  * The first green LED (telex, QD) should:
    * Be off when telex client is down
    * Blink slowly when connecting to the telex server
    * Be steady when connected to the telex server
    * Blink fast when receiving and printing a telex
  * The second green LED (VoIP, QE) should:
    * Be off when VoIP client is down
    * Blink slowly when connecting to the VoIP server
    * Be steady when connected to the VoIP server
    * Blink fast when receiving and/or sending audio
  * The orange LED (telegraphy, QC) should:
    * Have a quick blink every 2 seconds when telegraphy client is down
    * Blink slowly when connecting to the telegraphy server
    * Be off when connected to the telegraphy server EXCEPT while someone else in the same channel is pressing the telegraphy button.
* Audio:
  * A « bip » is emitted as long as someone else in the same channel is pressing the telegraphy button (can be turned on and off, volume and frequency can be changed from the local management console)
  * Everything broadcasted in the VoIP channel (except « loopback » ) is played on the speaker (volume can be changed from the local management console)
* Printer:
  * As soon as we receive a message, it is printed
  * If printing fails, it is queued for later and we try every 30s
  * If we got messages while the system was down, we print it at startup

### Management

* AP mode:
  When the Pi starts, it should look for known Wi-Fi networks (ideally we should be able to store Wi-Fi creds in a text file on the SD card). If it finds one, it connects to it. If it does not (or cannot connect) it should create an access-point called « bipbox » that we can connect to to do the setup.
* The Pi exposes a local management console (web).
  It advertises itself as `<<hostname>>.local` (by default: `bipbox.local` using mDNS.
  One can connect to the management console to tune:
  * The WiFi and networks: manage what wifi the Pi can connect to (add, remove, scan, …), maybe the addressing (static, DHCP, ...).
  * The hostname
  * The remote server(s?) address and credentials.
  * The printer settings (with a test feature).
  * The VoIP settings (volume, ...)
  * Anything that we think would be worse being tunable.

### Server

The server is self-hosted as a docker-compose and is designed to be exposed to the Internet through Cloudflare proxying.

* Admin console:
  The admin console should have strong security. Ideally a strong password with strong fail2ban + TOTP.
  It is accessible at `<<server-address>>/admin`
  It is used for:
  * Managing channels
    A channel is a group of bipboxes/owners. Bipboxes in the same channel can talk in VoIP and do telegraphy together.
  * Managing devices (bipboxes)/owners.
    Each bipbox should have a unique UUID. It must be part of a channel.
    It has a unique and strong authentication mechanism (that we can enter in the bipbox’s local management console) such as a long ID and key couple or even mTLS.
    It has an alias (such as the owner surname).
  * Managing web clients.
    We can create accounts for people that do not have a bipbox but can use a web interface to, at least, send Telex and, if possible, join the VoIP and telegraphy channel.
    Client accounts are attached to a channel. We can choose if they can communicate with the whole channel or only some devices.
    They each have a client ID and a password that should be strong.
* Client console:
  Clients can go to `<<server-address>>/<<channel>>` or `<<server-address>>/<<channel>>/<<device>>`.
  They are required to login. A hard fail2ban is applied (we are talking about an interface that can be used to communicate with children).
  From there, they can:
  * Manage their account (change password, ...)
  * Send telex to one or multiple recipients in the channel depending on the client’s permissions. Telex can be a text (through a textarea mimicking the printed result), a drawing (through a « paint-like » interface in the webpage) or a picture (converted to be printed in black and white).
  * Participate in the VoIP channel
  * Do telegraphy (a simple button + a light)

### Stack

We will use Python/Flask as much as possible.

### Embedding

Last step will be to produce a ready-to-flash (on a SD card) Raspberry Pi OS minimal image so we can flash a card with a bipbox-ready system and fire it up without having to tweak file, plug a keyboard, screen, or SSH to it.


# Appendices

## USB sound card specs

Raspberry-pi compatible

Brand Name	UGREEN
Hardware Interface	USB
Audio Output Mode	Stereo
Platform	Linux, Mac, Mac OS X, PlayStation 4, Windows 10
Hardware Platform	ARM
Model Name	UGREEN External USB Soundkarte
Surround Sound Channel Configuration	2.0
Signal-to-Noise Ratio	90 dB

You can plug in mic and headset at the same time, perfect for talking with friends while playing games, making videos on YouTube. The microphone input is in MONO. Note: The microphone jack only supports TRS jack (3-pole), does not support TS (2-pole) and TRRS (4-pole).

Universal Compatibility: This external USB sound card is compatible with PS5, PS4, PS4 Pro, desktop PC, laptop, Microsoft Surface, gaming headset, speaker, microphone, etc. Note: This USB jack adapter does not support PS3, Wii, Wii U, Xbox, TV, car.

No need to install additional drivers, just plug this USB audio adapter into the USB port and it will be automatically recognized by PC immediately. This USB sound card converter is compatible with multiple systems and supports Windows 11/ 10/ 8.1/ 8/ 7/ Vista/ XP, Mac OS, Chrome OS, Android and Linux systems. The driver must be installed for Linux systems.

This external stereo adapter has a built-in high-performance DAC smart chip with a sampling rate of up to 16bit/48kHz, which can prevent electromagnetic interference and reduce background noise. Oxygen-free copper cable ensures optimal sound quality and achieves stereo effect, the sound is clearer and more precise than the other USB sound card stick.

This USB audio to jack adapter is designed with an extension cord (15 cm). The body of the cord is flexible and can be bent and pulled at will without blocking the other USB plugs next to it, making it more practical than other USBs. It is small and does not take up space when carrying it, making it convenient to take along.

## Hub specs

This is a 4-port USB hub for Raspberry Pi Zero, and it can be mounted to Raspberry Pi Zero back-to-back. The 4 pogo pins on the back will connect the PP1, PP6, PP22 and PP23 testing pads on Raspberry Pi Zero, hence no soldering will be needed.

This is a 4-port USB hub for Raspberry Pi Zero, and it can be mounted to Raspberry Pi Zero back-to-back. After mounting this USB hub to your Raspberry Pi Zero, it immediately has 4 USB ports that could transfer data in USB 2.0 high-speed. The 4 pogo pins on the back will connect the PP1, PP6, PP22 and PP23 testing pads on Raspberry Pi Zero, hence no soldering will be needed.

The USB hub will take power directly from your Raspberry Pi Zero, so you don’t need to power the USB hub separately. However you can use the JST XH2.54 connector on board as the alternative power input port.

The blue LED on board works the power indicator, and will lights up when power is connected.

Each USB port has a dedicated white LED as transaction indicator, and a dedicated electrolytic capacitor to help stabilizing the output voltage.