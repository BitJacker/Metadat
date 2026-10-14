# Scarica foto e video di esempio pubblici per provare Metadat.
# Foto con GPS: github.com/ianare/exif-samples - Video GoPro con telemetria: github.com/gopro/gpmf-parser
$root = Split-Path $PSScriptRoot -Parent
$foto = Join-Path $root "test_foto"; $vid = Join-Path $root "test_video"
New-Item -ItemType Directory -Force $foto, $vid | Out-Null
$exif = "https://raw.githubusercontent.com/ianare/exif-samples/master/jpg"
foreach ($f in "gps/DSCN0010.jpg", "gps/DSCN0021.jpg", "gps/DSCN0042.jpg", "Canon_40D.jpg", "Nikon_D70.jpg") {
    Invoke-WebRequest "$exif/$f" -OutFile (Join-Path $foto (Split-Path $f -Leaf))
}
foreach ($f in "hero5.mp4", "hero6.mp4") {
    Invoke-WebRequest "https://raw.githubusercontent.com/gopro/gpmf-parser/main/samples/$f" -OutFile (Join-Path $vid $f)
}
Write-Host "Esempi scaricati in $foto e $vid"
