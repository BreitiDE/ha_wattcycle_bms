# Changelog

## 1.0.1 – 2026-11-07

### Behoben
- Schalten von Laden/Entladen meldete fälschlich „MOSFET hat nicht umgeschaltet“. Das BMS
  aktualisiert Status 3 erst mit Verzögerung; die Integration fragt den Status jetzt bis zu
  6 s lang alle 0,5 s ab, statt einmal nach 0,5 s. Fehlermeldung enthält den Rohwert von
  Status 3, jeder Abfrageschritt wird im Debug-Log protokolliert.

## 1.0.0 – 2026-10-30

Abgeglichen mit der offiziellen WattCycle-App 26.0.3 (`com.gz.wattcycle`).

### Neu
- Schalter **Laden** und **Entladen** (Register `0x0082` / `0x0083`, Funktion `0x06`).
  Ein- und Ausschalten sind möglich. Beim Start oder Neuladen wird **nie** geschaltet, der
  Zustand kommt immer aus dem BMS. Das Ausschalten lässt sich in den Optionen sperren.
  Nach jedem Schaltbefehl wird der Status neu gelesen und geprüft. Akzeptiert ein BMS
  Schreibbefehle nur mit Framing-Version 1, schaltet der Client automatisch um.
- Sensor **Aktive Alarme** mit Klartext (deutsch/englisch je nach HA-Sprache). Home Assistant
  zeichnet jede Änderung im Verlauf/Logbuch auf und ersetzt damit eine eigene Fehlerhistorie.
- Binärsensor **Warnung** (Vorwarnungen) zusätzlich zu **Problem** (Schutzfunktion oder Fehler).
- Zustände von Zellen, Temperatursensoren, Strömen und Packspannung als lesbare Werte
  (`normal`, `low_warning`, `high_warning`, `low_protection`, `high_protection`).
- Erweiterte Geräteinfo (112 Byte) mit Hardware-Version.

### Geändert
- Frame-Aufbau korrekt: Byte 2 Geräteadresse, Byte 3 Funktion, Byte 4–5 Registeradresse.
  Abgelehnte Befehle (`0x83`/`0x86`) werden erkannt.
- Erkennung wie in der App: Namen `WT…` und `XDZN…`. Batterien mit JK-BMS
  (Hersteller-ID 2917) oder JBD-BMS (8192 bzw. Dienst `FF00`) werden ausgeschlossen. Fehlt
  nach dem Verbinden der Dienst `FFF0`, bricht die Einrichtung mit klarer Meldung ab.
- Die Binärsensoren „Lade-/Entlade-MOSFET“ entfallen, die Schalter zeigen den Zustand.
- „Problem“ richtet sich nach den Status-Registern wie in der App, nicht mehr nach den
  Zustandsbytes (`0xF1` bei Firmware 1.x löste dort keinen Alarm aus).

## 0.1.0
- Erste lesende Version.
