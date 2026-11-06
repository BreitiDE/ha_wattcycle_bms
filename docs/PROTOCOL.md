# WattCycle / TDT BMS – Protokoll

Abgeglichen mit der offiziellen WattCycle-App 26.0.3 (`com.gz.wattcycle`, Klassen
`WattBleProtocolHandler`, `WarningInfo`, `BatteryDeviceService`). Ergänzt durch aiobmsble
(`tdt_bms.py`) und die Mitschnitte aus `rjeffl/LoRa-RAN`.

## Erkennung

Die App ordnet Namen mit `WT` bzw. `XDZN` dem Typ „WATT“ (TDT) zu. Unter `WT` laufen aber
auch Batterien mit anderem BMS:

| BMS | Erkennung | Dienst |
|---|---|---|
| TDT (diese Integration) | Name `WT…`/`XDZN…` | `FFF0` (Write `FFF2`, Notify `FFF1`, Unlock `FFFA`) |
| JK | Hersteller-ID 2917 (`0x0B65`) | `FFE0` |
| JBD | Hersteller-ID 8192 (`0x2000`) | `FF00` |

## Verbindung

1. Verbinden, `HiLink` auf `FFFA` schreiben (mit Response), Rücklesen ergibt `0x01`.
2. Notify auf `FFF1` aktivieren.
3. Kopfbyte ermitteln: Die App liest Register 30 zuerst mit `0x7E`, nach 3 s mit `0x1E`. Die
   Integration probiert `0x1E`, dann `0x7E`.

Ohne Entsperrung ignoriert das BMS alles und trennt nach etwa 4 s.

## Frame

```
[HEAD, VER, ADDR, FUNC, REG_HI, REG_LO, LEN_HI, LEN_LO, DATA…, CRC_HI, CRC_LO, 0x0D]
```

| Byte | Inhalt |
|---|---|
| HEAD | Anfrage `0x7E` oder `0x1E`, Antwort immer `0x7E` |
| VER | `0x00`. Die App nutzt `0x01` („neues Framing“), wenn Register 30 eine Version ≥ 1 mit erweiterten Daten meldet. |
| ADDR | Geräteadresse, Anfrage `0x01` |
| FUNC | `0x03` lesen, `0x06` schreiben. Gesetztes Bit 7 (`0x83`/`0x86`) heißt abgelehnt. |
| REG | 16-Bit-Registeradresse |
| LEN | Datenlänge (bei Leseanfragen 0) |
| CRC | CRC-16/MODBUS über alles davor, **big-endian** |

Firmware 1.x schickt `0x8D` mit falschem 1-Byte-CRC. Das wird toleriert.

## Register

| Register | App-Name | Richtung | Inhalt |
|---|---|---|---|
| `0x008C` (140) | Analogwerte | lesen | Zellen, Temperaturen, Pack |
| `0x008D` (141) | Warninfo | lesen | Zustände, Schutz, MOSFETs, Balancing |
| `0x0092` (146) | Produktinfo | lesen | Firmware, Hersteller, Seriennummer, Hardware |
| `0x0082` (130) | Ladeschalter | schreiben | `01` an, `00` aus |
| `0x0083` (131) | Entladeschalter | schreiben | `01` an, `00` aus |

Die App kennt weitere Register (Schutzschwellen 2–15/70–89/701–708, SOC 52, Kapazität 51,
Neustart 132, Werksreset 133, Passwort 120–122, Fehlerhistorie 10000). Diese Integration
nutzt sie bewusst nicht.

Schaltframes (Kopf `0x1E`):

```
Laden    an   1e 00 01 06 00 82 00 01 01 de 73 0d
Laden    aus  1e 00 01 06 00 82 00 01 00 1e b2 0d
Entladen an   1e 00 01 06 00 83 00 01 01 22 72 0d
Entladen aus  1e 00 01 06 00 83 00 01 00 e2 b3 0d
```

Die App verlangt dafür kein Passwort. Antwortet das BMS nicht auf `VER = 0x00`, versucht die
Integration `VER = 0x01`.

## 0x8C – Zellen und Pack (N Zellen, T Temperaturen, C = 2N + 2T + 2)

| Offset | Feld | Dekodierung |
|---|---|---|
| 0 | N | u8 |
| 1+2i | Zellspannung | u16 / 1000 V |
| 1+2N | T | u8 |
| 2+2N | MOSFET-Temperatur | (u16 − 2730) / 10 °C |
| 4+2N | Platinentemperatur | (u16 − 2730) / 10 °C |
| 6+2N+2j | Zelltemperatur j (T−2 Werte) | (u16 − 2730) / 10 °C |
| C | Strom | Bit 15 negativ, Bit 14 = 0,1 A, Bits 0–13 Betrag |
| C+2 | Spannung | u16 / 100 V |
| C+4 / +6 / +10 | Rest- / Voll- / Nennkapazität | u16, siehe Skalierung |
| C+8 | Zyklen | u16 |
| C+12 | SOC | u16 % |
| C+14 | SOH | u16 % (optional) |
| C+16 | kumulierte Kapazität | u32 (optional) |
| C+20 | Restlaufzeit | s32 Minuten (optional) |
| C+24 / +26 / +28 | Zeit bis Ruhemodus / Schutzereignisse / Notfallzeit | u16 (optional) |
| C+30 | Balancing-Strom | wie Strom (optional) |
| C+32+2k | Balancing-Messwerte, k < ⌈N/8⌉ | u16 / 1000 |
| danach | Zellen gesamt, Temps gesamt, Startindex Zellen, Startindex Temps | 4 × u8 |

**Skalierung der Kapazitäten:** Die App teilt immer durch 10. Die aufgezeichneten Frames von
Firmware 1.x passen nur ungeteilt. Die Integration erkennt das an der Firmware und lässt es
in den Optionen umstellen.

## 0x8D – Status (k = N + T)

| Offset | Feld |
|---|---|
| 1…N | Zellzustand |
| N+2, N+3 | Zustand MOSFET-, Platinentemperatur |
| N+4…N+T+1 | Zustand Zelltemperatursensoren |
| k+2 / k+3 / k+4 | Zustand Ladestrom / Packspannung / Entladestrom |
| k+5 | Betriebsart: 0 Standby, 1 Laden, 2 Entladen |
| k+6 | Status 1 (Schutz) |
| k+7 | Status 2 (Temperaturschutz) |
| k+8 | Status 3: Bit 1 Ladeschalter, Bit 2 Entladeschalter |
| k+9 | Status 4 (von der App nicht ausgewertet) |
| k+10 | Status 5 (Fehler) |
| k+11, k+12 | Status 6/7 (nicht ausgewertet) |
| k+13, k+14 | Warnung 1/2 |
| k+15… | Balancing-Bitmaske, ⌈N/8⌉ Bytes, Bit i = Zelle i+1 |
| danach (≥ 29 Byte) | 16 Byte reserviert, dann Status/Warnung 13–17 (Spannungs-/Temperaturdifferenz, Verpolung, Leitungsbruch, Chip-Fehler, Sekundärschutz). Die App liest sie, zeigt sie aber nicht an. |

**Zustandsbytes:** `00` normal, `01` Warnung niedrig, `02` Warnung hoch, `F0` Schutz
niedrig, `F1` Schutz hoch.

**Bits (Bit 0 → 7):**

| Register | Bedeutung |
|---|---|
| Status 1 | Zell-Überspannung, Zell-Unterspannung, Pack-Überspannung, Pack-Unterspannung, Überstrom Laden, Überstrom Entladen, Hardwareschutz, Ladespannung zu hoch |
| Status 2 | Übertemperatur Laden, Übertemperatur Entladen, Untertemperatur Laden, Untertemperatur Entladen, MOSFET-Übertemperatur, Umgebung zu heiß, Umgebung zu kalt |
| Status 5 | Lade-MOSFET-Fehler, Entlade-MOSFET-Fehler, Temperaturfehler, –, Zellfehler |
| Warnung 1 | wie Status 1 (Warnstufe) |
| Warnung 2 | Übertemperatur Laden, Übertemperatur Entladen, Untertemperatur Laden, Untertemperatur Entladen, **Umgebung zu heiß, Umgebung zu kalt, MOSFET zu heiß** (Reihenfolge anders als Status 2) |

Die Alarmliste der App besteht aus Status 1, 2, 5 und Warnung 1, 2. Die Zustandsbytes zählen
nicht dazu.

## 0x92 – Produktinfo

60 Byte: Firmware (20), Hersteller-Code (20), Seriennummer (20). Optional 112 Byte: zusätzlich
Hardware-Version (20), Hardware-Info (30), Messwiderstand-Flag (u16).
