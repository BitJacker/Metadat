# Metadat

App per Windows che mostra **tutte le informazioni nascoste** dentro foto, video e documenti:
dove è stata scattata una foto, con quale telefono o fotocamera, quando, e molto altro.

## Cosa legge

| Tipo | Informazioni |
|---|---|
| **Foto** (JPG, HEIC iPhone, PNG, TIFF, WebP, DNG) | dispositivo, obiettivo, data di scatto, tempo/diaframma/ISO, **posizione GPS con indirizzo e mappa**, altitudine, direzione |
| **Video** (MP4, MOV iPhone/Android, GoPro, ...) | dispositivo, data, durata, qualità, posizione, riproduzione |
| **Documenti** (PDF, Word, Excel, PowerPoint, OpenDocument) | autore, chi l'ha modificato, programma usato, date, pagine, macro |
| **Tutti i file** | dimensione, date, impronta SHA-256 |

### Funzioni
- **Normale / Dettagliata**: vista semplice con le cose importanti, oppure tutti i campi con ricerca.
- **Trascina e rilascia**, oppure **Ctrl+V** per incollare foto e video copiati da Esplora file.
- **Mappa** stradale o satellitare, apri in Google Maps / OpenStreetMap.
- **Live velocità**: per i video con traccia GPS (GoPro, droni DJI con file `.SRT`, oppure un file `.gpx`
  con lo stesso nome accanto al video) mostra tachimetro in km/h e posizione che si muove sulla mappa
  durante la riproduzione.
- **Copia info** e **Salva report** (TXT o JSON).

### Da sapere
- iPhone e Android salvano nei video solo il **punto di partenza**, non la velocità.
- WhatsApp, Telegram e i social **cancellano** i metadati: usa sempre il file originale.
- Indirizzo e mappa richiedono internet (OpenStreetMap / Esri). Tutto il resto funziona offline.

## Installazione

Scarica dalla pagina [Releases](../../releases):
- `Metadat-x.y.z-win64.msi` – installer (menu Start + collegamento sul desktop)
- `Metadat-portable.exe` – versione portatile, nessuna installazione

## Sviluppo

```powershell
pip install -r requirements.txt
python src/app.py                               # avvia l'app
python src/extractor.py foto.jpg                # metadati in JSON da riga di comando
powershell scripts/scarica_esempi.ps1           # file di esempio per provare
```

Build:

```powershell
pip install cx_Freeze pyinstaller
python installer/setup_msi.py bdist_msi         # -> dist/Metadat-x.y.z-win64.msi
cd src; pyinstaller --onefile --windowed --name Metadat-portable --icon metadat.ico --add-data "metadat.ico;." `
  --collect-all tkinterdnd2 --collect-all tkintermapview --collect-all pillow_heif --collect-all av app.py
```

### Struttura
- `src/extractor.py` – lettura metadati di foto e documenti
- `src/video.py` – metadati video e telemetria GPS (GoPro GPMF, CAMM, DJI SRT, GPX)
- `src/app.py` – interfaccia grafica (Tkinter)
- `src/mapfix.py` – correzione del consumo CPU della libreria mappa
