# WattCycle BMS für Home Assistant

Eigenständige Home-Assistant-Integration (`ha_wattcycle_bms`) für WattCycle-LiFePO4-Batterien
mit TDT-Smart-BMS über Bluetooth LE. Keine zusätzlichen Python-Abhängigkeiten, nur das, was
Home Assistant ohnehin mitbringt (`bleak`, `bleak-retry-connector`).

> Version 1.0.0. Lesen und Schalten von Laden/Entladen. Das Protokoll ist mit der offiziellen
> WattCycle-App 26.0.3 abgeglichen, siehe [docs/PROTOCOL.md](docs/PROTOCOL.md) und
> [CHANGELOG.md](CHANGELOG.md).

## Unterstützte Batterien

WattCycle-Batterien mit **TDT-BMS** (Bluetooth-Dienst `FFF0`). Erkannt werden Namen, die mit
`WT` oder `XDZN` beginnen, so wie in der App. WattCycle-Batterien mit **JK- oder JBD-BMS**
tragen ebenfalls `WT…`-Namen. Sie werden ausgefiltert bzw. mit einer klaren Meldung
abgelehnt. Bekannte Beispiele:

| Muster | Beispiel | Quelle |
|---|---|---|
| `WTDH…` | `WTDH04SC02` | 12V 314Ah Mini Super |
| `WT???AZ…` | `WTaHdAZ2603312531` | 12V 314Ah Mini (SKU WT12V314AH200MINIBTS) |
| `WT???BG…` | `WTbFcBG2621080` | 48V 50Ah Golf Cart (FW CLY-5673-V1.3) |
| `XDZN_…` | `XDZN_001_49A1` | ältere Mini-Modelle (früher „BMS Meta“-App) |

Andere TDT-Marken (Humsienk, Enerdrive …) werden bewusst ignoriert.

## Installation

### Über HACS (empfohlen)

1. HACS → ⋮ → *Benutzerdefinierte Repositories* → URL dieses Repositories eintragen,
   Kategorie **Integration**.
2. „WattCycle BMS“ in HACS herunterladen, Home Assistant neu starten.

### Manuell

1. Den Ordner `custom_components/ha_wattcycle_bms` aus dem Release-ZIP nach
   `<config>/custom_components/` kopieren.
2. Home Assistant neu starten.

### Einrichten

3. Batterie aufwecken (Last anlegen oder laden). Im Ruhezustand ist das BMS per
   Bluetooth nicht erreichbar.
4. Die Batterie wird automatisch gefunden, sonst *Einstellungen → Geräte & Dienste →
   Integration hinzufügen → WattCycle BMS*.

**Wichtig:** Läuft parallel `bms_ble` mit derselben Batterie, dort den Eintrag deaktivieren.
Das BMS erlaubt nur eine Verbindung gleichzeitig. Das gilt auch für die WattCycle-App.

## Entitäten

| Bereich | Entitäten |
|---|---|
| Pack | Spannung, Strom, Leistung, SOC, SOH*, Restkapazität, volle Kapazität, Nennkapazität, gespeicherte Energie, Zyklen, kumulierte Kapazität*, Restlaufzeit* |
| Zellen | Spannung je Zelle (Attribut `balancing`), Min/Max (Attribut `cell`), Mittelwert, Differenz (mV) |
| Temperaturen | MOSFET, Platine, Zellsensoren 1…n |
| Schalter | **Laden**, **Entladen** (siehe unten) |
| Status | Laden, Entladen, Balancing (Attribut `cells`), Betriebsart (Standby/Laden/Entladen) |
| Alarme | **Aktive Alarme** (Klartext), **Problem** (Schutzfunktion/Fehler), **Warnung** (Vorwarnung) |
| Diagnose | Fehlercode, Schutzereignisse*, Zeit bis Ruhemodus*, Notfallzeit*, Balancing-Strom*, RSSI, Rohbytes Status 1–7 / Warnung 1–2 / Zustände (standardmäßig deaktiviert) |

\* nur wenn die Firmware den Wert liefert. Entitäten werden nur für vorhandene Werte angelegt.

## Laden/Entladen schalten

Die beiden Schalter zeigen den Zustand, den das BMS meldet (Status 3).

- **Ein- und Ausschalten** sind möglich (UI, Automation, Sprachassistent).
- **Neustart und Neuladen schalten nie.** Die Integration sendet nur dann einen Schaltbefehl,
  wenn du (oder eine Automation) den Schalter bedienst. Ein Zustand wird weder gespeichert
  noch beim Start wiederhergestellt; nach einem Neustart zeigt der Schalter einfach wieder
  den echten Zustand aus dem BMS, normalerweise „an“.
- Wer das Ausschalten verhindern will (z. B. weil HA selbst an der Batterie hängt), schaltet
  in den Optionen *„Ausschalten von Laden/Entladen erlauben“* ab. Einschalten bleibt dann
  möglich, ein Ausschaltversuch endet mit einer Fehlermeldung.
- Nach jedem Befehl liest die Integration den Status neu. Hat der MOSFET nicht umgeschaltet
  (z. B. weil eine Schutzfunktion aktiv ist), gibt es eine Fehlermeldung.

> **Achtung:** „Entladen aus“ trennt den Batterieausgang. Hängt der Home-Assistant-Rechner
> selbst an dieser Batterie, schaltet er sich damit ab und kann nicht wieder einschalten. Das
> geht dann nur per WattCycle-App.

## Alarme

| Entität | Inhalt |
|---|---|
| Aktive Alarme | Klartext aller aktiven Schutzfunktionen, Fehler und Warnungen, sonst „Keine“. Jede Änderung landet im Verlauf. |
| Problem | an bei aktiver Schutzfunktion (Status 1/2) oder Hardwarefehler (Status 5) |
| Warnung | an bei Vorwarnung (Warnung 1/2) |

## Optionen

- **Abfrageintervall:** 10–600 s (Standard: 30 s)
- **Verbindung offen halten:** schneller und stabiler, belegt aber einen BLE-Verbindungsplatz
  (bei ESPHome-Proxys sind die knapp).
- **Ausschalten von Laden/Entladen erlauben:** standardmäßig an. Aus = nur Einschalten möglich.
- **Kapazitätsskalierung:** Firmware 1.x meldet Ah, ältere Firmware 0,1 Ah. *Automatisch*
  erkennt das an der Firmware-Version. Wenn Kapazitäten um Faktor 10 falsch sind, hier
  umstellen.

## Diagnosedaten

*Gerät → ⋮ → Diagnosedaten herunterladen* enthält die zuletzt empfangenen Roh-Frames
(`last_raw_frames`). Damit lassen sich unbekannte Status-Bits zuordnen.

## Entwicklung

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest          # Protokoll-, Client- und Integrationstests mit echten Frames
ruff check .
```

Protokolldetails: [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Lizenz

Apache-2.0. Das Protokoll basiert auf dem TDT-Plugin von
[aiobmsble](https://github.com/patman15/aiobmsble), siehe [NOTICE](NOTICE).
