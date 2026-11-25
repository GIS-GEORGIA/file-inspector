# ფაილის ამომცნობი · File Inspector

ფაილის ტიპს და შიგთავსს არკვევს **შიგთავსით და არა გაფართოებით**. მუშაობს
**QGIS-ის დანამატად** (QGIS 3.44+ Qt5-ზე, QGIS 4.x Qt6-ზე) და **ცალკე PyQt6 პროგრამად**.
ორენოვანია (ქართული / English), აქვს ღია, მუქი და ავტომატური თემა (მიჰყვება სისტემას ან QGIS-ს).

Identifies a file and what it contains **from its contents, not its name**. Runs as a
**QGIS plugin** (QGIS 3.44+ on Qt5, 4.x on Qt6) and as a **standalone PyQt6 app**.
Georgian / English, light, dark and automatic theme.

## როგორ ცნობს · How it identifies

1. **Magic bytes**: ხელმოწერა ფაილის დასაწყისში (PNG, PDF, ZIP, SQLite, TIFF, ELF, …)
2. **ტექსტის სტრუქტურა**: JSON/GeoJSON, XML (KML, GPX, GML, QGS/QML/QMD, SLD, OSM),
   CSV (გამყოფი, სათაური, კოორდინატების ან WKT სვეტი), WKT, CRS WKT (.prj), NMEA, PEM, INI
3. **კონტეინერის შიგნით**: ZIP-ის ფაილების სია → DOCX/XLSX/PPTX, ODF, EPUB, APK, JAR,
   KMZ, QGZ, ZIP-ში ჩადებული შეიპფაილი ან სხვა GIS მონაცემები
4. **ღრმა ანალიზი**: GeoPackage / SpatiaLite / MBTiles ცხრილები, GeoTIFF-ის კოორდინატთა სისტემა
   და პიქსელის ზომა, .shp/.shx/.dbf სათაურები, ფოტოს EXIF (კამერა, დრო, GPS), ენტროპია, strings

ფაილი **მხოლოდ იკითხება**: არაფერი ეშვება, არქივი არ იხსნება (მხოლოდ სია), XML იკითხება
ნიმუშებით (parser-ის გარეშე), SQLite იხსნება `mode=ro&immutable=1`-ით.

## ფუნქციები · Features

- დეტალები, შიგთავსი (ცხრილები, ველები, არქივის ფაილები, მონაცემების გადახედვა), Hex, strings
- გაფრთხილება: გაფართოება არ აქვს / არ ემთხვევა, დაშიფრული მონაცემები, zip-ბომბი,
  გაშვებადი კოდი, საიდუმლო გასაღები, შეიპფაილს აკლია .shx/.dbf/.prj
- **ასლის შენახვა სწორი გაფართოებით**
- **რუკაზე დამატება** (QGIS-ში): GeoPackage-ის ყველა ფენა, GeoJSON, KML/KMZ, GPX,
  CSV კოორდინატებით/WKT-ით, ZIP-ში შეიპფაილი, GeoTIFF… გაფართოების გარეშეც
- ფენაზე მარჯვენა ღილაკი → **„ფენის ფაილის შემოწმება“**
- ანგარიშის კოპირება მიმდინარე ენაზე

## გაშვება · Running

**ცალკე პროგრამა · Standalone**

```bash
pip install -r requirements.txt
python3 -m file_inspector            # ან: python3 -m file_inspector /path/to/file
```

**QGIS დანამატი · QGIS plugin**

```bash
scripts/package.sh                   # → dist/file_inspector-0.1.0.zip
```

QGIS → Plugins → Manage and Install Plugins → Install from ZIP.

## ტესტები · Tests

```bash
python3 -m unittest discover -s tests -v
```

## სტრუქტურა · Layout

```
file_inspector/
  core/detector.py      ამოცნობა: მხოლოდ სტანდარტული Python, Qt-ის გარეშე
  core/i18n.py          ქართული / ინგლისური ტექსტები
  ui/qt_compat.py       qgis.PyQt (QGIS-ში) ან PyQt6 (ცალკე), PyQt5 სათადარიგოდ
  ui/theme.py           ღია / მუქი / ავტომატური თემა
  ui/inspector_widget.py  საერთო პანელი
  plugin.py             QGIS-ის დანამატი (dock პანელი + ფენის მენიუ)
  app.py, __main__.py   ცალკე პროგრამა
```

License: GPL-3.0-or-later.
