"""Identify a file by its content, not its name.

Pure Python (standard library only, no Qt), so it runs inside QGIS, in the
standalone app and in tests alike. The file is only read, never executed,
extracted or modified: ZIP members are listed (and at most a few KB of a
member are read), XML is examined with patterns instead of a parser, and
SQLite databases are opened read-only and immutable.

`analyze(path)` returns a dict:

    path, name, size, extension
    kind          short id, e.g. "geojson", "gpkg", "png"
    label         human readable format name
    mime
    category      one of CATEGORIES
    confidence    "high" | "medium" | "low"
    expected_ext  extensions this format normally has
    ext_mismatch  True when the file's extension does not fit the content
    details       [(i18n_key, value), ...]
    contents      {"title": i18n_key, "columns": [i18n_key|str], "rows": [[...]]} or None
    warnings      [(i18n_key, {format args}), ...]
    qgis_layers   [{"uri", "name", "provider", "type"}]  (type: vector|raster)
    entropy       bits per byte of the sampled data
    head          first bytes (for the hex view)
    strings       printable strings found in the sample
"""

import csv
import io
import json
import math
import os
import re
import sqlite3
import struct
import urllib.parse
import zipfile

HEAD_SIZE = 64 * 1024          # bytes read for signature and text checks
SAMPLE_SIZE = 1024 * 1024      # bytes read for entropy and strings
JSON_FULL_PARSE_LIMIT = 30 * 1024 * 1024
HEX_BYTES = 512

CATEGORIES = ("gis_vector", "gis_raster", "gis_other", "image", "document",
              "spreadsheet", "archive", "database", "text", "code", "font", "security",
              "audio", "video", "executable", "unknown")

# kind -> (label, mime, category, expected extensions)
FORMATS = {
    "png": ("PNG image", "image/png", "image", ["png"]),
    "jpeg": ("JPEG image", "image/jpeg", "image", ["jpg", "jpeg"]),
    "gif": ("GIF image", "image/gif", "image", ["gif"]),
    "bmp": ("BMP image", "image/bmp", "image", ["bmp"]),
    "webp": ("WebP image", "image/webp", "image", ["webp"]),
    "ico": ("Windows icon", "image/x-icon", "image", ["ico"]),
    "psd": ("Photoshop document", "image/vnd.adobe.photoshop", "image", ["psd"]),
    "svg": ("SVG vector image", "image/svg+xml", "image", ["svg"]),
    "tiff": ("TIFF image", "image/tiff", "image", ["tif", "tiff"]),
    "geotiff": ("GeoTIFF raster", "image/tiff", "gis_raster", ["tif", "tiff"]),
    "bigtiff": ("BigTIFF raster", "image/tiff", "gis_raster", ["tif", "tiff"]),
    "jp2": ("JPEG 2000 image", "image/jp2", "gis_raster", ["jp2"]),
    "pdf": ("PDF document", "application/pdf", "document", ["pdf"]),
    "rtf": ("Rich Text document", "application/rtf", "document", ["rtf"]),
    "postscript": ("PostScript document", "application/postscript", "document", ["ps", "eps"]),
    "docx": ("Word document (DOCX)", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "document", ["docx"]),
    "xlsx": ("Excel workbook (XLSX)", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "spreadsheet", ["xlsx"]),
    "pptx": ("PowerPoint presentation (PPTX)", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "document", ["pptx"]),
    "odt": ("OpenDocument text", "application/vnd.oasis.opendocument.text", "document", ["odt"]),
    "ods": ("OpenDocument spreadsheet", "application/vnd.oasis.opendocument.spreadsheet", "spreadsheet", ["ods"]),
    "odp": ("OpenDocument presentation", "application/vnd.oasis.opendocument.presentation", "document", ["odp"]),
    "epub": ("EPUB e-book", "application/epub+zip", "document", ["epub"]),
    "ole2": ("Microsoft Office 97-2003 / OLE2 (doc, xls, ppt, msi)", "application/x-ole-storage", "document", ["doc", "xls", "ppt", "msi"]),
    "zip": ("ZIP archive", "application/zip", "archive", ["zip"]),
    "kmz": ("KMZ (zipped KML)", "application/vnd.google-earth.kmz", "gis_vector", ["kmz"]),
    "shp_zip": ("Zipped shapefile", "application/zip", "gis_vector", ["zip"]),
    "gis_zip": ("ZIP archive with GIS data", "application/zip", "gis_vector", ["zip"]),
    "qgz": ("QGIS project (QGZ)", "application/x-qgis-project", "gis_other", ["qgz"]),
    "apk": ("Android app (APK)", "application/vnd.android.package-archive", "executable", ["apk"]),
    "jar": ("Java archive (JAR)", "application/java-archive", "executable", ["jar"]),
    "gzip": ("gzip compressed data", "application/gzip", "archive", ["gz", "tgz"]),
    "bzip2": ("bzip2 compressed data", "application/x-bzip2", "archive", ["bz2"]),
    "xz": ("xz compressed data", "application/x-xz", "archive", ["xz"]),
    "zstd": ("Zstandard compressed data", "application/zstd", "archive", ["zst"]),
    "7z": ("7-Zip archive", "application/x-7z-compressed", "archive", ["7z"]),
    "rar": ("RAR archive", "application/vnd.rar", "archive", ["rar"]),
    "tar": ("TAR archive", "application/x-tar", "archive", ["tar"]),
    "elf": ("Linux/Unix executable (ELF)", "application/x-executable", "executable", ["", "so", "o"]),
    "pe": ("Windows executable (PE)", "application/vnd.microsoft.portable-executable", "executable", ["exe", "dll"]),
    "macho": ("macOS executable (Mach-O)", "application/x-mach-binary", "executable", ["", "dylib"]),
    "java_class": ("Java class file", "application/java-vm", "executable", ["class"]),
    "wasm": ("WebAssembly module", "application/wasm", "executable", ["wasm"]),
    "sqlite": ("SQLite database", "application/vnd.sqlite3", "database", ["sqlite", "db", "sqlite3"]),
    "gpkg": ("GeoPackage", "application/geopackage+sqlite3", "gis_vector", ["gpkg"]),
    "spatialite": ("SpatiaLite database", "application/vnd.sqlite3", "gis_vector", ["sqlite", "db"]),
    "mbtiles": ("MBTiles tile set", "application/vnd.mapbox-vector-tile", "gis_raster", ["mbtiles"]),
    "shp": ("Shapefile geometry (.shp)", "application/x-esri-shape", "gis_vector", ["shp"]),
    "shx": ("Shapefile index (.shx)", "application/x-esri-shape", "gis_other", ["shx"]),
    "sbn": ("Shapefile spatial index (.sbn / .sbx)", "application/octet-stream", "gis_other", ["sbn", "sbx"]),
    "qmd": ("QGIS layer metadata (QMD)", "application/xml", "gis_other", ["qmd"]),
    "ttf": ("TrueType font", "font/ttf", "font", ["ttf"]),
    "otf": ("OpenType font", "font/otf", "font", ["otf"]),
    "woff": ("WOFF web font", "font/woff", "font", ["woff"]),
    "woff2": ("WOFF2 web font", "font/woff2", "font", ["woff2"]),
    "pem_private": ("Private key (PEM)", "application/x-pem-file", "security", ["pem", "key", ""]),
    "pem": ("Certificate / public key (PEM)", "application/x-pem-file", "security", ["pem", "crt", "cer", "pub"]),
    "ini": ("Configuration file (INI / TOML)", "text/plain", "text", ["ini", "cfg", "conf", "toml", "service"]),
    "dbf": ("dBASE table (.dbf)", "application/x-dbf", "database", ["dbf"]),
    "fgb": ("FlatGeobuf", "application/flatgeobuf", "gis_vector", ["fgb"]),
    "las": ("LAS point cloud", "application/vnd.las", "gis_other", ["las", "laz"]),
    "parquet": ("Apache Parquet / GeoParquet", "application/vnd.apache.parquet", "gis_vector", ["parquet"]),
    "hdf5": ("HDF5 data", "application/x-hdf5", "gis_raster", ["h5", "hdf5", "he5"]),
    "netcdf": ("NetCDF data", "application/x-netcdf", "gis_raster", ["nc"]),
    "grib": ("GRIB weather data", "application/x-grib", "gis_raster", ["grib", "grb", "grib2"]),
    "wav": ("WAV audio", "audio/wav", "audio", ["wav"]),
    "mp3": ("MP3 audio", "audio/mpeg", "audio", ["mp3"]),
    "flac": ("FLAC audio", "audio/flac", "audio", ["flac"]),
    "ogg": ("Ogg media", "audio/ogg", "audio", ["ogg", "oga", "ogv"]),
    "mp4": ("MP4 / QuickTime video", "video/mp4", "video", ["mp4", "m4a", "mov", "3gp"]),
    "avi": ("AVI video", "video/x-msvideo", "video", ["avi"]),
    "mkv": ("Matroska / WebM video", "video/x-matroska", "video", ["mkv", "webm"]),
    "geojson": ("GeoJSON", "application/geo+json", "gis_vector", ["geojson", "json"]),
    "topojson": ("TopoJSON", "application/json", "gis_vector", ["topojson", "json"]),
    "json": ("JSON data", "application/json", "text", ["json"]),
    "kml": ("KML (Google Earth)", "application/vnd.google-earth.kml+xml", "gis_vector", ["kml"]),
    "gpx": ("GPX (GPS track)", "application/gpx+xml", "gis_vector", ["gpx"]),
    "gml": ("GML (Geography Markup Language)", "application/gml+xml", "gis_vector", ["gml"]),
    "osm_xml": ("OpenStreetMap XML", "application/vnd.openstreetmap.data+xml", "gis_vector", ["osm"]),
    "qgs": ("QGIS project (QGS)", "application/x-qgis-project", "gis_other", ["qgs"]),
    "qml": ("QGIS layer style (QML)", "application/xml", "gis_other", ["qml"]),
    "sld": ("SLD style", "application/vnd.ogc.sld+xml", "gis_other", ["sld"]),
    "html": ("HTML page", "text/html", "code", ["html", "htm"]),
    "xml": ("XML document", "application/xml", "text", ["xml"]),
    "prj": ("Projection definition (WKT CRS)", "text/plain", "gis_other", ["prj", "wkt"]),
    "wkt": ("WKT geometry", "text/plain", "gis_vector", ["wkt", "txt"]),
    "csv": ("CSV / delimited table", "text/csv", "spreadsheet", ["csv", "tsv", "txt"]),
    "csv_points": ("CSV with coordinates", "text/csv", "gis_vector", ["csv", "tsv", "txt"]),
    "nmea": ("NMEA GPS log", "text/plain", "gis_vector", ["nmea", "txt", "log"]),
    "world_file": ("World file (raster georeference)", "text/plain", "gis_other", ["tfw", "jgw", "pgw", "wld"]),
    "script": ("Script", "text/x-script", "code", ["sh", "py"]),
    "text": ("Plain text", "text/plain", "text", ["txt"]),
    "empty": ("Empty file", "application/x-empty", "unknown", []),
    "unknown": ("Unknown binary data", "application/octet-stream", "unknown", []),
}

# (offset, signature, kind); checked in order, first match wins
SIGNATURES = [
    (0, b"\x89PNG\r\n\x1a\n", "png"),
    (0, b"\xff\xd8\xff", "jpeg"),
    (0, b"GIF87a", "gif"),
    (0, b"GIF89a", "gif"),
    (0, b"II*\x00", "tiff"),
    (0, b"MM\x00*", "tiff"),
    (0, b"II+\x00", "bigtiff"),
    (0, b"MM\x00+", "bigtiff"),
    (0, b"\x00\x00\x00\x0cjP  \r\n\x87\n", "jp2"),
    (0, b"\xff\x4f\xff\x51", "jp2"),
    (0, b"8BPS", "psd"),
    (0, b"\x00\x00\x01\x00", "ico"),
    (0, b"%PDF-", "pdf"),
    (0, b"{\\rtf", "rtf"),
    (0, b"%!PS", "postscript"),
    (0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole2"),
    (0, b"PK\x03\x04", "zip"),
    (0, b"PK\x05\x06", "zip"),
    (0, b"\x1f\x8b", "gzip"),
    (0, b"BZh", "bzip2"),
    (0, b"\xfd7zXZ\x00", "xz"),
    (0, b"\x28\xb5\x2f\xfd", "zstd"),
    (0, b"7z\xbc\xaf\x27\x1c", "7z"),
    (0, b"Rar!\x1a\x07", "rar"),
    (257, b"ustar", "tar"),
    (0, b"\x7fELF", "elf"),
    (0, b"\xcf\xfa\xed\xfe", "macho"),
    (0, b"\xce\xfa\xed\xfe", "macho"),
    (0, b"\xca\xfe\xba\xbe", "java_class"),
    (0, b"\x00asm", "wasm"),
    (0, b"SQLite format 3\x00", "sqlite"),
    (0, b"\x00\x00\x27\x0a", "shp"),
    (0, b"fgb\x03", "fgb"),
    (0, b"wOFF", "woff"),
    (0, b"wOF2", "woff2"),
    (0, b"OTTO", "otf"),
    (0, b"\x00\x01\x00\x00\x00", "ttf"),
    (0, b"true\x00", "ttf"),
    (0, b"LASF", "las"),
    (0, b"PAR1", "parquet"),
    (0, b"\x89HDF\r\n\x1a\n", "hdf5"),
    (0, b"CDF\x01", "netcdf"),
    (0, b"CDF\x02", "netcdf"),
    (0, b"GRIB", "grib"),
    (0, b"ID3", "mp3"),
    (0, b"\xff\xfb", "mp3"),
    (0, b"\xff\xf3", "mp3"),
    (0, b"fLaC", "flac"),
    (0, b"OggS", "ogg"),
    (0, b"\x1a\x45\xdf\xa3", "mkv"),
    (0, b"MZ", "pe"),
    (0, b"BM", "bmp"),
]

SHAPE_TYPES = {0: "Null", 1: "Point", 3: "Polyline", 5: "Polygon", 8: "MultiPoint",
               11: "PointZ", 13: "PolylineZ", 15: "PolygonZ", 18: "MultiPointZ",
               21: "PointM", 23: "PolylineM", 25: "PolygonM", 28: "MultiPointM",
               31: "MultiPatch"}

ELF_MACHINES = {3: "x86", 40: "ARM", 62: "x86-64", 183: "AArch64", 243: "RISC-V"}

LAT_NAMES = {"lat", "latitude", "y", "lat_dd", "point_y", "northing", "განედი", "y_coord", "ycoord"}
LON_NAMES = {"lon", "lng", "long", "longitude", "x", "lon_dd", "point_x", "easting", "გრძედი", "x_coord", "xcoord"}
WKT_RE = re.compile(r"^\s*(SRID=\d+;\s*)?(POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|"
                    r"MULTIPOLYGON|GEOMETRYCOLLECTION)\s*(Z|M|ZM)?\s*(\(|EMPTY)", re.I)
CRS_WKT_RE = re.compile(r"^\s*(PROJCS|GEOGCS|GEOCCS|COMPD_CS|PROJCRS|GEOGCRS|GEODCRS|COMPOUNDCRS|BOUNDCRS)\s*\[", re.I)


# --------------------------------------------------------------------------- helpers

def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return str(n)


def entropy(data):
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    return -sum(c / n * math.log2(c / n) for c in counts if c)


def printable_strings(data, min_len=6, limit=400):
    out = []
    for m in re.finditer(rb"[\x20-\x7e\t]{%d,}" % min_len, data):
        out.append(m.group().decode("ascii", "replace").strip())
        if len(out) >= limit:
            break
    # UTF-8 runs containing non-ASCII letters (e.g. Georgian text inside binaries)
    for m in re.finditer(rb"(?:[\xc2-\xf4][\x80-\xbf]{1,3}|[\x20-\x7e]){%d,}" % min_len, data):
        chunk = m.group()
        if any(b >= 0x80 for b in chunk):
            try:
                out.append(chunk.decode("utf-8").strip())
            except UnicodeDecodeError:
                pass
        if len(out) >= limit * 2:
            break
    seen, uniq = set(), []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq[:limit]


def _result(path, kind, confidence="high"):
    label, mime, category, exts = FORMATS[kind]
    return {"kind": kind, "label": label, "mime": mime, "category": category,
            "expected_ext": list(exts), "confidence": confidence,
            "details": [], "contents": None, "warnings": [], "qgis_layers": []}


def _set_kind(res, kind, confidence=None):
    label, mime, category, exts = FORMATS[kind]
    res.update(kind=kind, label=label, mime=mime, category=category, expected_ext=list(exts))
    if confidence:
        res["confidence"] = confidence


def _layer(uri, name, provider, ltype):
    return {"uri": uri, "name": name, "provider": provider, "type": ltype}


def _stem(path):
    return os.path.splitext(os.path.basename(path))[0] or os.path.basename(path)


# --------------------------------------------------------------------------- entry point

def analyze(path):
    path = os.path.abspath(path)
    size = os.path.getsize(path)
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    with open(path, "rb") as f:
        sample = f.read(SAMPLE_SIZE)
    head = sample[:HEAD_SIZE]

    if size == 0:
        res = _result(path, "empty")
    else:
        res = None
        for offset, sig, kind in SIGNATURES:
            if head[offset:offset + len(sig)] == sig:
                res = _result(path, kind)
                break
        if res is None and head[:4] == b"RIFF" and len(head) >= 12:
            sub = head[8:12]
            res = _result(path, {b"WEBP": "webp", b"WAVE": "wav", b"AVI ": "avi"}.get(sub, "unknown"),
                          "high" if sub in (b"WEBP", b"WAVE", b"AVI ") else "low")
        if res is None and head[4:8] == b"ftyp":
            res = _result(path, "mp4")
        if res is None and _looks_like_dbf(head, size):
            res = _result(path, "dbf", "medium")
        if res is None:
            res = _analyze_text(path, head, size)
        if res is None and zipfile.is_zipfile(path):
            res = _result(path, "zip", "medium")   # ZIP with data in front (self-extracting, .pyz)
        if res is None:
            res = _result(path, "unknown", "low")
        else:
            try:
                _deepen(res, path, head, size)
            except Exception as e:  # a damaged file must not break the report
                res["warnings"].append(("warn_parse_failed", {"error": str(e)}))
                res["confidence"] = "medium" if res["confidence"] == "high" else res["confidence"]

    res.update(path=path, name=os.path.basename(path), size=size, extension=ext,
               head=sample[:HEX_BYTES], entropy=entropy(sample),
               strings=printable_strings(sample) if res["category"] not in ("text", "code") or res["kind"] in ("unknown",) else [])
    res["ext_mismatch"] = bool(res["expected_ext"]) and ext not in res["expected_ext"] and res["kind"] not in ("unknown", "empty")
    if res["ext_mismatch"]:
        if not ext:
            res["warnings"].insert(0, ("warn_no_ext", {"ext": res["expected_ext"][0]}))
        else:
            res["warnings"].insert(0, ("warn_ext_mismatch", {"ext": ext, "expected": ", ".join("." + e for e in res["expected_ext"] if e)}))
    if res["kind"] == "unknown" and res["entropy"] > 7.9 and size > 4096:
        res["warnings"].append(("warn_high_entropy", {}))
    if res["kind"] == "pem_private":
        res["warnings"].append(("warn_private_key", {}))
    if res["category"] == "executable":
        res["warnings"].append(("warn_executable", {}))
    if res["kind"] == "shp" and ext != "shp" and res["qgis_layers"]:
        # OGR only opens shapefiles by extension
        res["qgis_layers"] = []
        res["warnings"].append(("warn_shp_needs_ext", {}))
    return res


def _deepen(res, path, head, size):
    handler = {
        "png": _png, "jpeg": _jpeg, "gif": _gif, "bmp": _bmp,
        "tiff": _tiff, "bigtiff": _bigtiff, "jp2": _gdal_raster,
        "pdf": _pdf, "zip": _zip, "sqlite": _sqlite, "shp": _shp, "dbf": _dbf,
        "elf": _elf, "pe": _pe, "gzip": _gzip, "ole2": _ole2,
        "fgb": _ogr_vector, "parquet": _ogr_vector, "netcdf": _gdal_raster,
        "hdf5": _gdal_raster, "grib": _gdal_raster, "las": _las,
    }.get(res["kind"])
    if handler:
        handler(res, path, head, size)


# --------------------------------------------------------------------------- images

def _png(res, path, head, size):
    if head[12:16] == b"IHDR":
        w, h, depth, ctype = struct.unpack(">IIBB", head[16:26])
        res["details"] += [("d_dimensions", f"{w} × {h} px"), ("d_bit_depth", depth),
                           ("d_color", {0: "Grayscale", 2: "RGB", 3: "Palette", 4: "Gray+Alpha", 6: "RGBA"}.get(ctype, ctype))]


def _gif(res, path, head, size):
    w, h = struct.unpack("<HH", head[6:10])
    res["details"].append(("d_dimensions", f"{w} × {h} px"))
    res["details"].append(("d_version", head[:6].decode("ascii")))


def _bmp(res, path, head, size):
    if len(head) >= 26:
        declared = struct.unpack("<I", head[2:6])[0]
        w, h = struct.unpack("<ii", head[18:26])
        if declared not in (size, 0) or w <= 0 or w > 100000:
            _set_kind(res, "unknown", "low")  # "BM" occurs by chance
            return
        res["details"].append(("d_dimensions", f"{w} × {abs(h)} px"))


def _jpeg(res, path, head, size):
    i = 2
    exif = None
    while i + 4 < len(head):
        if head[i] != 0xFF:
            break
        marker = head[i + 1]
        seg_len = struct.unpack(">H", head[i + 2:i + 4])[0]
        if marker == 0xE1 and head[i + 4:i + 10] == b"Exif\x00\x00":
            exif = head[i + 10:i + 2 + seg_len]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", head[i + 5:i + 9])
            res["details"].append(("d_dimensions", f"{w} × {h} px"))
            break
        i += 2 + seg_len
    if exif:
        _exif(res, exif)


def _exif(res, data):
    """Camera, date and GPS position from EXIF (photo from the field)."""
    try:
        t = _TiffReader(lambda off, n: data[off:off + n], len(data))
        ifd0 = t.read_ifd(t.first_ifd)
        for tag, key in ((0x010F, "d_camera_make"), (0x0110, "d_camera_model")):
            if tag in ifd0:
                res["details"].append((key, t.value(ifd0[tag])))
        if 0x8769 in ifd0:
            sub = t.read_ifd(t.value(ifd0[0x8769]))
            if 0x9003 in sub:
                taken = str(t.value(sub[0x9003]))
                res["details"].append(("d_date_taken", re.sub(r"^(\d{4}):(\d\d):(\d\d)", r"\1-\2-\3", taken)))
        lat = lon = None
        if 0x8825 in ifd0:
            gps = t.read_ifd(t.value(ifd0[0x8825]))
            lat = _gps_coord(t, gps, 2, 1)
            lon = _gps_coord(t, gps, 4, 3)
            if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
                res["details"].append(("d_gps", f"{lat:.6f}, {lon:.6f}"))
                res["details"].append(("d_gps_link", f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=17/{lat:.6f}/{lon:.6f}"))
                if 6 in gps:
                    alt = t.value(gps[6])
                    if isinstance(alt, (int, float)):
                        res["details"].append(("d_altitude", f"{alt:.1f} m"))
            else:
                lat = None
        if lat is None:
            res["details"].append(("d_gps", "—"))
    except Exception:
        pass


def _gps_coord(t, gps, value_tag, ref_tag):
    if value_tag not in gps:
        return None
    v = t.value(gps[value_tag])
    if not isinstance(v, list) or len(v) < 3:
        return None
    deg = v[0] + v[1] / 60.0 + v[2] / 3600.0
    ref = t.value(gps[ref_tag]) if ref_tag in gps else ""
    if isinstance(ref, str) and ref[:1] in ("S", "W"):
        deg = -deg
    return deg


class _TiffReader:
    """Minimal classic-TIFF IFD reader working on any random-access source."""
    TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 16: 8}

    def __init__(self, read_at, size):
        self.read_at = read_at
        self.size = size
        bo = read_at(0, 2)
        if bo == b"II":
            self.e = "<"
        elif bo == b"MM":
            self.e = ">"
        else:
            raise ValueError("not TIFF")
        self.first_ifd = struct.unpack(self.e + "I", read_at(4, 4))[0]

    def read_ifd(self, offset):
        n = struct.unpack(self.e + "H", self.read_at(offset, 2))[0]
        if n > 1000:
            raise ValueError("bad IFD")
        raw = self.read_at(offset + 2, n * 12)
        entries = {}
        for i in range(n):
            tag, typ, count = struct.unpack(self.e + "HHI", raw[i * 12:i * 12 + 8])
            entries[tag] = (typ, count, raw[i * 12 + 8:i * 12 + 12])
        return entries

    def value(self, entry, max_items=4096):
        typ, count, field = entry
        size = self.TYPE_SIZES.get(typ, 1) * count
        data = field[:size] if size <= 4 else self.read_at(struct.unpack(self.e + "I", field)[0], min(size, max_items * 8))
        if typ == 2:
            return data.split(b"\x00")[0].decode("latin-1", "replace").strip()
        count = min(count, max_items)
        fmt = {1: "B", 3: "H", 4: "I", 6: "b", 8: "h", 9: "i", 11: "f", 12: "d", 16: "Q"}.get(typ)
        if typ in (5, 10):
            vals = struct.unpack(self.e + ("I" if typ == 5 else "i") * (2 * count), data[:8 * count])
            out = [vals[i] / vals[i + 1] if vals[i + 1] else 0.0 for i in range(0, len(vals), 2)]
        elif fmt:
            out = list(struct.unpack(self.e + fmt * count, data[:struct.calcsize(fmt) * count]))
        else:
            return data
        return out[0] if count == 1 else out


GEOKEY_NAMES = {1024: "GTModelType", 1025: "GTRasterType", 2048: "GeographicType", 3072: "ProjectedCSType", 4096: "VerticalCSType"}


def _tiff(res, path, head, size):
    with open(path, "rb") as f:
        def read_at(off, n):
            f.seek(off)
            return f.read(n)
        t = _TiffReader(read_at, size)
        ifd = t.read_ifd(t.first_ifd)
        w = t.value(ifd[256]) if 256 in ifd else "?"
        h = t.value(ifd[257]) if 257 in ifd else "?"
        res["details"].append(("d_dimensions", f"{w} × {h} px"))
        if 277 in ifd:
            res["details"].append(("d_bands", t.value(ifd[277])))
        if 258 in ifd:
            bits = t.value(ifd[258])
            res["details"].append(("d_bit_depth", bits[0] if isinstance(bits, list) else bits))
        if 259 in ifd:
            comp = t.value(ifd[259])
            res["details"].append(("d_compression", {1: "None", 5: "LZW", 7: "JPEG", 8: "Deflate", 32773: "PackBits",
                                                     34887: "LERC", 50000: "ZSTD", 50001: "WebP"}.get(comp, comp)))
        if 322 in ifd:
            res["details"].append(("d_tiled", f"{t.value(ifd[322])} px"))
        geo = any(tag in ifd for tag in (33550, 33922, 34264, 34735))
        if geo:
            _set_kind(res, "geotiff")
            if 34735 in ifd:
                keys = t.value(ifd[34735])
                if isinstance(keys, list) and len(keys) >= 4:
                    for i in range(4, min(len(keys), 4 + 4 * keys[3]), 4):
                        kid, loc, _cnt, val = keys[i:i + 4]
                        if kid in (2048, 3072) and loc == 0 and val not in (0, 32767):
                            res["details"].append(("d_crs", f"EPSG:{val}"))
            if 33550 in ifd:
                scale = t.value(ifd[33550])
                if isinstance(scale, list):
                    res["details"].append(("d_pixel_size", f"{scale[0]:g} × {scale[1]:g}"))
            if 42113 in ifd:
                res["details"].append(("d_nodata", t.value(ifd[42113])))
            res["qgis_layers"].append(_layer(path, _stem(path), "gdal", "raster"))
        else:
            world = [s for s in (".tfw", ".tifw", ".wld") if os.path.exists(os.path.splitext(path)[0] + s)]
            if world:
                _set_kind(res, "geotiff", "medium")
                res["details"].append(("d_world_file", ", ".join(world)))
                res["qgis_layers"].append(_layer(path, _stem(path), "gdal", "raster"))


def _bigtiff(res, path, head, size):
    res["qgis_layers"].append(_layer(path, _stem(path), "gdal", "raster"))


def _gdal_raster(res, path, head, size):
    res["qgis_layers"].append(_layer(path, _stem(path), "gdal", "raster"))


def _ogr_vector(res, path, head, size):
    res["qgis_layers"].append(_layer(path, _stem(path), "ogr", "vector"))


def _las(res, path, head, size):
    if len(head) >= 27:
        res["details"].append(("d_version", f"{head[24]}.{head[25]}"))
    if len(head) >= 111:
        n = struct.unpack("<I", head[107:111])[0]
        res["details"].append(("d_points", f"{n:,}"))


# --------------------------------------------------------------------------- documents / binaries

def _pdf(res, path, head, size):
    res["details"].append(("d_version", head[5:8].decode("ascii", "replace")))
    with open(path, "rb") as f:
        data = f.read(min(size, 8 * 1024 * 1024))
    pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
    if pages:
        res["details"].append(("d_pages", pages if size <= len(data) else f"≥ {pages}"))
    if b"/Encrypt" in data:
        res["details"].append(("d_encrypted", "yes"))
    if b"/LGIDict" in data or b"/Measure" in data and b"/GEO" in data:
        res["details"].append(("d_geopdf", "yes"))
        res["qgis_layers"].append(_layer(path, _stem(path), "gdal", "raster"))
    title = re.search(rb"/Title\s*\(([^)]{1,200})\)", data)
    if title:
        res["details"].append(("d_title", title.group(1).decode("latin-1", "replace")))


def _elf(res, path, head, size):
    if len(head) >= 20:
        cls = {1: "32-bit", 2: "64-bit"}.get(head[4], "?")
        endian = "<" if head[5] == 1 else ">"
        machine = struct.unpack(endian + "H", head[18:20])[0]
        etype = struct.unpack(endian + "H", head[16:18])[0]
        res["details"] += [("d_arch", f"{ELF_MACHINES.get(machine, machine)} {cls}"),
                           ("d_type", {1: "object", 2: "executable", 3: "shared library / PIE", 4: "core dump"}.get(etype, etype))]


def _pe(res, path, head, size):
    if len(head) < 64:
        _set_kind(res, "unknown", "low")
        return
    off = struct.unpack("<I", head[60:64])[0]
    if head[off:off + 4] != b"PE\x00\x00":
        _set_kind(res, "unknown", "low")
        return
    machine = struct.unpack("<H", head[off + 4:off + 6])[0]
    chars = struct.unpack("<H", head[off + 22:off + 24])[0]
    res["details"].append(("d_arch", {0x14c: "x86", 0x8664: "x86-64", 0xaa64: "ARM64"}.get(machine, hex(machine))))
    res["details"].append(("d_type", "DLL" if chars & 0x2000 else "EXE"))


def _gzip(res, path, head, size):
    flags = head[3] if len(head) > 3 else 0
    if flags & 0x08:
        end = head.find(b"\x00", 10)
        if end > 10:
            name = head[10:end].decode("latin-1", "replace")
            res["details"].append(("d_original_name", name))
            if name.lower().endswith(".tar"):
                res["details"].append(("d_type", "tar.gz"))
    res["qgis_layers"] = []


def _ole2(res, path, head, size):
    with open(path, "rb") as f:
        data = f.read(min(size, 2 * 1024 * 1024))
    for marker, label, kind_cat in ((b"W\x00o\x00r\x00d\x00D\x00o\x00c", "Word 97-2003 (.doc)", "document"),
                                    (b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k", "Excel 97-2003 (.xls)", "spreadsheet"),
                                    (b"P\x00o\x00w\x00e\x00r\x00P\x00o\x00i\x00n\x00t", "PowerPoint 97-2003 (.ppt)", "document")):
        if marker in data:
            res["label"] = label
            res["category"] = kind_cat
            res["expected_ext"] = [label.split("(.")[1].rstrip(")")]
            break


# --------------------------------------------------------------------------- archives

def _zip(res, path, head, size):
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        lower = [n.lower() for n in names]
        total = sum(i.file_size for i in infos)
        res["details"] += [("d_members", len(infos)), ("d_uncompressed", human_size(total))]
        if size and total / size > 200:
            res["warnings"].append(("warn_zip_bomb", {"ratio": int(total / size)}))
        if any(i.flag_bits & 0x1 for i in infos):
            res["details"].append(("d_encrypted", "yes"))

        mimetype = ""
        if "mimetype" in names:
            mimetype = z.open("mimetype").read(200).decode("ascii", "replace").strip()
        kind = None
        if "[content_types].xml" in lower:
            if any(n.startswith("word/") for n in lower):
                kind = "docx"
            elif any(n.startswith("xl/") for n in lower):
                kind = "xlsx"
            elif any(n.startswith("ppt/") for n in lower):
                kind = "pptx"
        elif mimetype:
            kind = {"application/vnd.oasis.opendocument.text": "odt",
                    "application/vnd.oasis.opendocument.spreadsheet": "ods",
                    "application/vnd.oasis.opendocument.presentation": "odp",
                    "application/epub+zip": "epub"}.get(mimetype)
        if kind is None:
            if "androidmanifest.xml" in lower and any(n.endswith(".dex") for n in lower):
                kind = "apk"
            elif "meta-inf/manifest.mf" in lower:
                kind = "jar"
            elif any(n.endswith(".qgs") for n in lower):
                kind = "qgz"
            elif any(n.endswith(".kml") for n in lower) and not any(n.endswith(".shp") for n in lower):
                kind = "kmz"
            elif any(n.endswith(".shp") for n in lower):
                kind = "shp_zip"
            elif any(n.endswith(e) for n in lower for e in (".gpkg", ".geojson", ".gml", ".gpx", ".tif", ".tiff", ".fgb")):
                kind = "gis_zip"
        if kind:
            _set_kind(res, kind)

        vsi = "/vsizip/{" + path + "}"   # braces: GDAL then needs no .zip/.kmz extension
        if kind == "kmz":
            kml = next(n for n in names if n.lower().endswith(".kml"))
            res["qgis_layers"].append(_layer(f"{vsi}/{kml}", _stem(path), "ogr", "vector"))
        elif kind in ("shp_zip", "gis_zip"):
            for n in names:
                ln = n.lower()
                if ln.endswith((".shp", ".gpkg", ".geojson", ".gml", ".gpx", ".kml", ".fgb")):
                    res["qgis_layers"].append(_layer(f"{vsi}/{n}", _stem(n), "ogr", "vector"))
                elif ln.endswith((".tif", ".tiff")):
                    res["qgis_layers"].append(_layer(f"{vsi}/{n}", _stem(n), "gdal", "raster"))
            shps = [n for n in lower if n.endswith(".shp")]
            for s in shps:
                missing = [e for e in (".shx", ".dbf") if s[:-4] + e not in lower]
                if missing:
                    res["warnings"].append(("warn_shp_missing", {"name": s, "missing": ", ".join(missing)}))
                if s[:-4] + ".prj" not in lower:
                    res["warnings"].append(("warn_no_prj", {"name": s}))

        rows = []
        for i in infos[:2000]:
            rows.append([i.filename, human_size(i.file_size), human_size(i.compress_size),
                         "%04d-%02d-%02d %02d:%02d" % i.date_time[:5]])
        res["contents"] = {"title": "c_members", "columns": ["col_name", "col_size", "col_packed", "col_modified"], "rows": rows}
        if len(infos) > 2000:
            res["warnings"].append(("warn_truncated", {"n": 2000, "total": len(infos)}))


# --------------------------------------------------------------------------- databases

def _sqlite(res, path, head, size):
    app_id = struct.unpack(">I", head[68:72])[0] if len(head) >= 72 else 0
    uri = "file:" + urllib.parse.quote(path) + "?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.cursor()
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tset = set(tables)
        res["details"].append(("d_tables", len(tables)))
        res["details"].append(("d_sqlite_version", f"page size {struct.unpack('>H', head[16:18])[0]}"))

        geo_tables = {}
        if app_id in (0x47504B47, 0x47503130) or "gpkg_contents" in tset:
            _set_kind(res, "gpkg")
            for name, dtype, srs in cur.execute("SELECT table_name, data_type, srs_id FROM gpkg_contents"):
                geo_tables[name] = (dtype, srs)
            if "gpkg_geometry_columns" in tset:
                for tname, gtype in cur.execute("SELECT table_name, geometry_type_name FROM gpkg_geometry_columns"):
                    if tname in geo_tables:
                        geo_tables[tname] = (geo_tables[tname][0] + f" · {gtype}", geo_tables[tname][1])
            for name, (dtype, srs) in geo_tables.items():
                if dtype.startswith("features"):
                    res["qgis_layers"].append(_layer(f"{path}|layername={name}", name, "ogr", "vector"))
                elif dtype.startswith("tiles") or dtype.startswith("2d-gridded"):
                    res["qgis_layers"].append(_layer(f"GPKG:{path}:{name}", name, "gdal", "raster"))
        elif "metadata" in tset and "tiles" in tset:
            _set_kind(res, "mbtiles")
            meta = dict(cur.execute("SELECT name, value FROM metadata").fetchall())
            for k in ("name", "format", "minzoom", "maxzoom", "bounds"):
                if k in meta:
                    res["details"].append((f"d_mb_{k}", meta[k]))
            res["qgis_layers"].append(_layer(path, meta.get("name") or _stem(path), "gdal", "raster"))
        elif "geometry_columns" in tset and "spatial_ref_sys" in tset:
            _set_kind(res, "spatialite")
            try:
                for tname, gcol, srid in cur.execute("SELECT f_table_name, f_geometry_column, srid FROM geometry_columns"):
                    geo_tables[tname] = (f"geometry: {gcol}", srid)
                    res["qgis_layers"].append(_layer(f"{path}|layername={tname}", tname, "ogr", "vector"))
            except sqlite3.Error:
                pass

        rows = []
        for t in tables[:300]:
            try:
                cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{t}")')]
                n = cur.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            except sqlite3.Error:
                cols, n = [], "?"
            info = geo_tables.get(t)
            rows.append([t, n, len(cols), (f"{info[0]} · SRS {info[1]}" if info else ""), ", ".join(cols[:20])])
        res["contents"] = {"title": "c_tables", "columns": ["col_table", "col_rows", "col_fields", "col_spatial", "col_field_names"], "rows": rows}
    finally:
        con.close()


def _looks_like_dbf(head, size):
    if len(head) < 33 or head[0] not in (0x02, 0x03, 0x04, 0x05, 0x30, 0x31, 0x43, 0x63, 0x83, 0x8B, 0xCB, 0xF5, 0xFB):
        return False
    mm, dd = head[2], head[3]
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return False
    nrec, hlen, rlen = struct.unpack("<IHH", head[4:12])
    if hlen < 33 or rlen < 1 or hlen > len(head):
        return False
    if head[hlen - 1] != 0x0D:
        return False
    return abs(hlen + nrec * rlen - size) <= 1  # optional 0x1A end-of-file byte


def _dbf(res, path, head, size):
    nrec, hlen, rlen = struct.unpack("<IHH", head[4:12])
    fields = []
    for off in range(32, hlen - 1, 32):
        d = head[off:off + 32]
        if len(d) < 32 or d[0] == 0x0D:
            break
        name = d[:11].split(b"\x00")[0].decode("latin-1", "replace")
        fields.append([name, chr(d[11]), d[16], d[17]])
    res["details"] += [("d_records", nrec), ("d_fields", len(fields)),
                       ("d_last_update", f"{1900 + head[1]}-{head[2]:02d}-{head[3]:02d}")]
    if head[29]:
        res["details"].append(("d_codepage_id", head[29]))
    cpg = os.path.splitext(path)[0] + ".cpg"
    if os.path.exists(cpg):
        with open(cpg, encoding="latin-1") as f:
            res["details"].append(("d_encoding", f.read(50).strip()))
    res["contents"] = {"title": "c_fields", "columns": ["col_name", "col_type", "col_length", "col_decimals"], "rows": fields}
    res["qgis_layers"].append(_layer(path, _stem(path), "ogr", "vector"))


def _shp(res, path, head, size):
    if len(head) < 100:
        _set_kind(res, "unknown", "low")
        return
    flen = struct.unpack(">I", head[24:28])[0] * 2
    version, stype = struct.unpack("<II", head[28:36])
    xmin, ymin, xmax, ymax = struct.unpack("<4d", head[36:68])
    if version != 1000:
        if version == 0 and flen == size:
            _set_kind(res, "sbn")
            return
        _set_kind(res, "unknown", "low")
        return
    first = struct.unpack(">I", head[100:104])[0] if len(head) >= 104 else None
    if first == 50 or (first is None and os.path.splitext(path)[1].lower() == ".shx"):
        _set_kind(res, "shx")
        res["details"].append(("d_features", (size - 100) // 8))
    res["details"] += [("d_geometry", SHAPE_TYPES.get(stype, stype)),
                       ("d_extent", f"{xmin:.6g}, {ymin:.6g} — {xmax:.6g}, {ymax:.6g}")]
    if flen != size:
        res["warnings"].append(("warn_size_mismatch", {"declared": flen, "actual": size}))
    if -180.5 <= xmin <= xmax <= 180.5 and -90.5 <= ymin <= ymax <= 90.5:
        res["details"].append(("d_crs_guess", "WGS 84 / geographic (degrees)"))
    elif 200000 <= xmin <= 900000 and 4400000 <= ymin <= 4800000:
        res["details"].append(("d_crs_guess", "UTM 38N (EPSG:32638)? — Georgia"))
    base = os.path.splitext(path)[0]
    sidecars = []
    for e in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qmd", ".sbn"):
        sidecars.append([e, "✓" if os.path.exists(base + e) else "✗"])
    res["contents"] = {"title": "c_sidecars", "columns": ["col_file", "col_present"], "rows": sidecars}
    if res["kind"] == "shp":
        if not os.path.exists(base + ".prj"):
            res["warnings"].append(("warn_no_prj", {"name": os.path.basename(path)}))
        res["qgis_layers"].append(_layer(path, _stem(path), "ogr", "vector"))


# --------------------------------------------------------------------------- text formats

def _decode(head):
    if head.startswith(b"\xef\xbb\xbf"):
        return head[3:].decode("utf-8", "replace"), "UTF-8 (BOM)"
    if head.startswith(b"\xff\xfe"):
        return head[2:].decode("utf-16-le", "replace"), "UTF-16 LE"
    if head.startswith(b"\xfe\xff"):
        return head[2:].decode("utf-16-be", "replace"), "UTF-16 BE"
    if len(head) > 8 and head[1::2].count(0) > len(head) * 0.4 and head[0::2].count(0) < len(head) * 0.05:
        return head.decode("utf-16-le", "replace"), "UTF-16 LE"
    if b"\x00" in head:
        return None, None
    try:
        return head.decode("utf-8"), "UTF-8" if any(b > 0x7f for b in head) else "ASCII"
    except UnicodeDecodeError as e:
        if e.start > len(head) - 4:   # multi-byte character cut by the sample boundary
            return head[:e.start].decode("utf-8"), "UTF-8"
    text = head.decode("cp1252", "replace")
    printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
    if printable / max(len(text), 1) < 0.95:
        return None, None
    return text, "8-bit (Windows-1252 / ANSI?)"


def _analyze_text(path, head, size):
    text, encoding = _decode(head)
    if text is None:
        return None
    stripped = text.lstrip("﻿ \t\r\n")
    res = None
    for check in (_check_pem, _check_json, _check_ini, _check_xml, _check_crs_wkt, _check_wkt, _check_nmea, _check_script,
                  _check_world_file, _check_csv):
        res = check(path, stripped, size)
        if res:
            break
    if res is None:
        res = _result(path, "text", "medium")
    res["details"].insert(0, ("d_encoding", encoding))
    lines = text.count("\n")
    res["details"].insert(1, ("d_lines", lines if size <= len(head) else f"~{int(lines * size / max(len(head), 1)):,}"))
    return res


def _check_json(path, text, size):
    if text[:1] not in ("{", "[") or not re.match(r'^[\[{]\s*(["{\[\]}\d-]|true|false|null|$)', text):
        return None
    data = None
    if size <= JSON_FULL_PARSE_LIMIT:
        try:
            with open(path, "rb") as f:
                raw = f.read()
            data = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            data = None
    head_type = re.search(r'"type"\s*:\s*"(\w+)"', text[:4000])
    head_type = head_type.group(1) if head_type else ""
    if isinstance(data, dict):
        t = data.get("type", "")
        if t == "Topology":
            res = _result(path, "topojson")
            res["details"].append(("d_objects", ", ".join(data.get("objects", {}).keys())))
            res["qgis_layers"].append(_layer(path, _stem(path), "ogr", "vector"))
            return res
        if t in ("FeatureCollection", "Feature", "Point", "LineString", "Polygon", "MultiPoint",
                 "MultiLineString", "MultiPolygon", "GeometryCollection"):
            return _geojson(path, data)
    if data is None and head_type in ("FeatureCollection", "Feature"):
        res = _result(path, "geojson", "medium")
        res["qgis_layers"].append(_layer(path, _stem(path), "ogr", "vector"))
        if size > JSON_FULL_PARSE_LIMIT:
            res["details"].append(("d_note", "large file: summary from the beginning only"))
        else:
            res["warnings"].append(("warn_parse_failed", {"error": "invalid JSON"}))
        return res
    if data is None and size <= JSON_FULL_PARSE_LIMIT:
        return None   # not valid JSON: let the other checks look at it
    res = _result(path, "json", "high" if data is not None else "medium")
    if isinstance(data, dict):
        res["details"].append(("d_top_keys", ", ".join(list(data.keys())[:30])))
    elif isinstance(data, list):
        res["details"].append(("d_items", len(data)))
        if data and isinstance(data[0], dict):
            keys = list(data[0].keys())
            res["contents"] = {"title": "c_preview", "columns": keys[:30],
                               "rows": [[_short(r.get(k)) for k in keys[:30]] for r in data[:200] if isinstance(r, dict)]}
    return res


def _short(v, n=120):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"


def _geojson(path, data):
    res = _result(path, "geojson")
    feats = data.get("features") if data.get("type") == "FeatureCollection" else [data] if data.get("type") == "Feature" else []
    feats = feats or []
    gtypes, keys = {}, []
    for ft in feats:
        g = (ft or {}).get("geometry") or {}
        gtypes[g.get("type", "null")] = gtypes.get(g.get("type", "null"), 0) + 1
        for k in ((ft or {}).get("properties") or {}):
            if k not in keys:
                keys.append(k)
    if not feats and data.get("type") not in ("FeatureCollection", "Feature"):
        gtypes[data["type"]] = 1
    res["details"] += [("d_features", len(feats)),
                       ("d_geometry", ", ".join(f"{k} ({v})" for k, v in gtypes.items())),
                       ("d_fields", len(keys))]
    crs = (data.get("crs") or {}).get("properties", {}).get("name")
    res["details"].append(("d_crs", crs or "EPSG:4326 (RFC 7946)"))
    bbox = _coords_bbox(feats[:5000])
    if bbox:
        res["details"].append(("d_extent", "%.6g, %.6g — %.6g, %.6g" % bbox))
    res["contents"] = {"title": "c_attributes", "columns": keys[:40],
                       "rows": [[_short(((ft or {}).get("properties") or {}).get(k, "")) for k in keys[:40]] for ft in feats[:300]]}
    res["qgis_layers"].append(_layer(path, _stem(path), "ogr", "vector"))
    return res


def _coords_bbox(feats):
    xs, ys = [], []

    def walk(c):
        if isinstance(c, (list, tuple)):
            if len(c) >= 2 and all(isinstance(v, (int, float)) for v in c[:2]):
                xs.append(c[0])
                ys.append(c[1])
            else:
                for cc in c:
                    walk(cc)
    for ft in feats:
        g = (ft or {}).get("geometry") or {}
        walk(g.get("coordinates"))
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _xml_root(text):
    t = re.sub(r"<\?.*?\?>|<!--.*?-->|<!DOCTYPE[^>]*>", "", text[:20000], flags=re.S).lstrip()
    m = re.match(r"<([\w.:-]+)", t)
    return (m.group(1), t) if m else (None, t)


def _check_xml(path, text, size):
    if not text.startswith("<"):
        return None
    low = text[:3000].lower()
    if low.startswith("<!doctype html") or low.startswith("<html") or "<html" in low[:500]:
        res = _result(path, "html")
        title = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
        if title:
            res["details"].append(("d_title", title.group(1).strip()[:200]))
        return res
    root, body = _xml_root(text)
    if not root:
        return None
    local = root.split(":")[-1]
    l = local.lower()
    if l == "kml":
        res = _result(path, "kml")
        _count_tags(res, path, size, {"d_placemarks": b"<Placemark", "d_folders": b"<Folder",
                                      "d_ground_overlays": b"<GroundOverlay"})
        name = re.search(r"<name>(.*?)</name>", text, re.S)
        if name:
            res["details"].append(("d_title", name.group(1).strip()[:200]))
        res["qgis_layers"].append(_layer(path, _stem(path), "ogr", "vector"))
    elif l == "gpx":
        res = _result(path, "gpx")
        creator = re.search(r'creator="([^"]*)"', text[:2000])
        if creator:
            res["details"].append(("d_creator", creator.group(1)))
        _count_tags(res, path, size, {"d_waypoints": b"<wpt", "d_tracks": b"<trk>", "d_track_points": b"<trkpt",
                                      "d_routes": b"<rte>"})
        for lt in ("waypoints", "tracks", "routes", "track_points"):
            res["qgis_layers"].append(_layer(f"{path}|layername={lt}", f"{_stem(path)} · {lt}", "ogr", "vector"))
    elif l == "svg":
        res = _result(path, "svg")
    elif l == "qgis":
        head20 = text[:20000]
        is_project = "<projectlayers" in head20 or "projectname=" in head20[:2000]
        is_style = not is_project and ("styleCategories" in head20[:3000] or "<renderer-v2" in head20)
        is_meta = not is_project and not is_style and "<identifier" in head20
        res = _result(path, "qmd" if is_meta else "qml" if is_style else "qgs")
        ver = re.search(r'version="([^"]+)"', text[:2000])
        if ver:
            res["details"].append(("d_qgis_version", ver.group(1)))
        if is_project:
            _count_tags(res, path, size, {"d_layers": b"<maplayer"})
    elif l == "styledlayerdescriptor":
        res = _result(path, "sld")
    elif l == "osm":
        res = _result(path, "osm_xml")
        _count_tags(res, path, size, {"d_nodes": b"<node", "d_ways": b"<way", "d_relations": b"<relation"})
        res["qgis_layers"].append(_layer(path, _stem(path), "ogr", "vector"))
    elif "opengis.net/gml" in body[:3000] or l in ("featurecollection",) and "gml" in body[:3000].lower():
        res = _result(path, "gml")
        _count_tags(res, path, size, {"d_features": b"featureMember"})
        res["qgis_layers"].append(_layer(path, _stem(path), "ogr", "vector"))
    else:
        res = _result(path, "xml")
        res["details"].append(("d_root", root))
    return res


def _count_tags(res, path, size, tags):
    """Count occurrences of tags by streaming the file (no XML parsing)."""
    counts = {k: 0 for k in tags}
    overlap = 32
    with open(path, "rb") as f:
        prev = b""
        while True:
            chunk = f.read(4 * 1024 * 1024)
            if not chunk:
                break
            buf = prev + chunk
            for k, tag in tags.items():
                counts[k] += buf.count(tag) - prev.count(tag)
            prev = buf[-overlap:]
    for k, v in counts.items():
        if v:
            res["details"].append((k, f"{v:,}"))


def _check_pem(path, text, size):
    m = re.match(r"-----BEGIN ([A-Z0-9 ]+)-----", text)
    if not m:
        return None
    what = m.group(1)
    res = _result(path, "pem_private" if "PRIVATE KEY" in what else "pem")
    res["details"].append(("d_type", what.title()))
    res["details"].append(("d_blocks", text.count("-----BEGIN ")))
    return res


def _check_ini(path, text, size):
    lines = [ln.strip() for ln in text.splitlines()[:200] if ln.strip() and not ln.strip().startswith(("#", ";"))]
    if not lines:
        return None
    sections = [ln for ln in lines if re.match(r"^\[\[?[\w .\"'-]+\]\]?$", ln)]
    pairs = [ln for ln in lines if re.match(r"^[\w.\"'-]+\s*[=:]", ln)]
    if sections and len(sections) + len(pairs) >= len(lines) * 0.8:
        res = _result(path, "ini")
        res["details"].append(("d_sections", ", ".join(s.strip("[]") for s in sections[:20])))
        return res
    return None


def _check_crs_wkt(path, text, size):
    m = CRS_WKT_RE.match(text)
    if not m:
        return None
    res = _result(path, "prj")
    name = re.search(r'^\s*\w+\s*\[\s*"([^"]+)"', text)
    if name:
        res["details"].append(("d_crs", name.group(1)))
    epsg = re.findall(r'(?:AUTHORITY\["EPSG",\s*"?|ID\["EPSG",\s*)(\d+)', text)
    if epsg:
        res["details"].append(("d_epsg", f"EPSG:{epsg[-1]}"))
    return res


def _check_wkt(path, text, size):
    lines = [ln for ln in text.splitlines() if ln.strip()][:20]
    if lines and all(WKT_RE.match(ln) for ln in lines):
        res = _result(path, "wkt")
        res["details"].append(("d_geometry", WKT_RE.match(lines[0]).group(2).upper()))
        return res
    return None


def _check_nmea(path, text, size):
    lines = [ln for ln in text.splitlines()[:50] if ln.strip()]
    if len(lines) >= 2 and sum(bool(re.match(r"^\$(GP|GN|GL|GA|BD)[A-Z]{3},", ln)) for ln in lines) >= len(lines) * 0.6:
        res = _result(path, "nmea")
        kinds = sorted({ln[3:6] for ln in lines if ln.startswith("$")})
        res["details"].append(("d_sentences", ", ".join(kinds)))
        return res
    return None


def _check_script(path, text, size):
    if not text.startswith("#!"):
        return None
    res = _result(path, "script")
    first = text.splitlines()[0]
    res["details"].append(("d_interpreter", first[2:].strip()))
    if "python" in first:
        res["label"] = "Python script"
        res["expected_ext"] = ["py", ""]
    elif re.search(r"\b(ba|z|da)?sh\b", first):
        res["label"] = "Shell script"
        res["expected_ext"] = ["sh", ""]
    res["warnings"].append(("warn_executable", {}))
    return res


def _check_world_file(path, text, size):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) != 6:
        return None
    try:
        vals = [float(v) for v in lines]
    except ValueError:
        return None
    res = _result(path, "world_file", "medium")
    res["details"] += [("d_pixel_size", f"{vals[0]:g} × {vals[3]:g}"), ("d_origin", f"{vals[4]:.6g}, {vals[5]:.6g}")]
    return res


def _check_csv(path, text, size):
    lines = text.splitlines()
    sample_lines = [ln for ln in lines[:200] if ln.strip()]
    if len(sample_lines) < 2:
        return None
    try:
        dialect = csv.Sniffer().sniff("\n".join(sample_lines[:50]), delimiters=",;\t|")
    except csv.Error:
        return None
    rows = list(csv.reader(io.StringIO("\n".join(sample_lines[:301])), dialect))
    widths = [len(r) for r in rows]
    if widths[0] < 2 or sum(w == widths[0] for w in widths) < len(widths) * 0.8:
        return None
    header = rows[0]
    has_header = True
    try:
        has_header = csv.Sniffer().has_header("\n".join(sample_lines[:50]))
    except csv.Error:
        pass
    if not has_header:
        header = [f"field_{i + 1}" for i in range(widths[0])]
        data_rows = rows
    else:
        data_rows = rows[1:]
    delim = {"\t": "TAB", ",": ",", ";": ";", "|": "|"}.get(dialect.delimiter, dialect.delimiter)
    norm = [h.strip().strip('"').lower() for h in header]
    lat = next((header[i] for i, h in enumerate(norm) if h in LAT_NAMES), None)
    lon = next((header[i] for i, h in enumerate(norm) if h in LON_NAMES), None)
    wkt_col = None
    if data_rows:
        for i, v in enumerate(data_rows[0]):
            if WKT_RE.match(v or ""):
                wkt_col = header[i]
                break
    kind = "csv_points" if (lat and lon) or wkt_col else "csv"
    res = _result(path, kind, "high" if len(sample_lines) > 3 else "medium")
    res["details"] += [("d_delimiter", delim), ("d_columns", len(header)), ("d_has_header", "yes" if has_header else "no")]
    # the delimitedtext provider wants the delimiter as is (tab written as \t)
    qdelim = "%5Ct" if dialect.delimiter == "\t" else dialect.delimiter
    base = f"{_file_url(path)}?type=csv&delimiter={qdelim}&detectTypes=yes&useHeader={'yes' if has_header else 'no'}"
    if wkt_col:
        res["details"].append(("d_wkt_column", wkt_col))
        res["qgis_layers"].append(_layer(f"{base}&wktField={urllib.parse.quote(wkt_col)}&crs=EPSG:4326",
                                         _stem(path), "delimitedtext", "vector"))
    elif lat and lon:
        res["details"].append(("d_coord_columns", f"X = {lon}, Y = {lat}"))
        crs = "EPSG:4326"
        try:
            xs = [float(r[header.index(lon)]) for r in data_rows[:50] if len(r) == len(header) and r[header.index(lon)].strip()]
            if xs and max(abs(x) for x in xs) > 180:
                crs = "EPSG:32638"
                res["details"].append(("d_crs_guess", "projected coordinates (UTM 38N? — check)"))
        except ValueError:
            pass
        res["qgis_layers"].append(_layer(f"{base}&xField={urllib.parse.quote(lon)}&yField={urllib.parse.quote(lat)}&crs={crs}",
                                         _stem(path), "delimitedtext", "vector"))
    res["contents"] = {"title": "c_preview", "columns": header, "rows": data_rows[:300]}
    return res


def _file_url(path):
    p = path.replace("\\", "/")
    return ("file:///" if not p.startswith("/") else "file://") + urllib.parse.quote(p)


# --------------------------------------------------------------------------- report text

def report_text(res, t):
    """Plain-text report in the current language; `t` is the translate function."""
    out = [f"{t('report_title')}: {res['name']}", "=" * 60,
           f"{t('lbl_type')}: {res['label']}", f"{t('lbl_category')}: {t('cat_' + res['category'])}",
           f"MIME: {res['mime']}", f"{t('lbl_confidence')}: {t('conf_' + res['confidence'])}",
           f"{t('lbl_size')}: {human_size(res['size'])} ({res['size']:,} B)",
           f"{t('lbl_entropy')}: {res['entropy']:.2f} / 8", f"{t('lbl_path')}: {res['path']}"]
    if res["warnings"]:
        out.append("")
        out += ["⚠ " + t(k, **a) for k, a in res["warnings"]]
    if res["details"]:
        out += ["", t("tab_details") + ":"]
        out += [f"  {t(k)}: {t('v_' + v) if isinstance(v, str) and v in ('yes', 'no') else v}" for k, v in res["details"]]
    return "\n".join(out)
