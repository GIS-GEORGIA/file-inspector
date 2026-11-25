"""Detector tests: every sample is written WITHOUT an extension, so the
format must be recognised from the content alone.

    python3 -m unittest discover -s tests -v
"""

import json
import os
import sqlite3
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from file_inspector.core import detector, i18n  # noqa: E402


def _tiff_with_geokeys(path, epsg=32638):
    """Tiny 1x1 GeoTIFF built by hand (no GDAL needed)."""
    e = "<"
    entries = []
    extra = b""
    base = 8 + 2 + 12 * 6 + 4          # header + IFD
    def add(tag, typ, count, value_bytes):
        nonlocal extra
        if len(value_bytes) <= 4:
            entries.append(struct.pack(e + "HHI", tag, typ, count) + value_bytes.ljust(4, b"\0"))
        else:
            entries.append(struct.pack(e + "HHII", tag, typ, count, base + len(extra)))
            extra += value_bytes
    add(256, 3, 1, struct.pack(e + "H", 1))
    add(257, 3, 1, struct.pack(e + "H", 1))
    add(258, 3, 1, struct.pack(e + "H", 8))
    add(277, 3, 1, struct.pack(e + "H", 1))
    add(33550, 12, 3, struct.pack(e + "3d", 10.0, 10.0, 0.0))
    keys = [1, 1, 0, 2, 1024, 0, 1, 1, 3072, 0, 1, epsg]
    add(34735, 3, len(keys), struct.pack(e + f"{len(keys)}H", *keys))
    ifd = struct.pack(e + "H", len(entries)) + b"".join(entries) + struct.pack(e + "I", 0)
    with open(path, "wb") as f:
        f.write(b"II*\x00" + struct.pack(e + "I", 8) + ifd + extra + b"\x00")


def _dbf(path, rows):
    fields = [("NAME", "C", 20, 0), ("AREA", "N", 10, 2)]
    hlen = 32 + 32 * len(fields) + 1
    rlen = 1 + sum(f[2] for f in fields)
    out = struct.pack("<BBBBIHH20x", 0x03, 126, 11, 25, len(rows), hlen, rlen)
    for name, typ, ln, dec in fields:
        out += name.encode().ljust(11, b"\0") + typ.encode() + b"\0" * 4 + bytes([ln, dec]) + b"\0" * 14
    out += b"\x0d"
    for name, area in rows:
        out += b" " + name.encode().ljust(20) + f"{area:10.2f}".encode()
    out += b"\x1a"
    with open(path, "wb") as f:
        f.write(out)


def _shp(path, n_points=2):
    content_len = (100 + n_points * (8 + 20)) // 2
    hdr = struct.pack(">7I", 9994, 0, 0, 0, 0, 0, content_len)
    hdr += struct.pack("<2I4d4d", 1000, 1, 44.7, 41.6, 44.9, 41.8, 0, 0, 0, 0)
    body = b""
    for i in range(n_points):
        body += struct.pack(">2I", i + 1, 10) + struct.pack("<I2d", 1, 44.7 + i * 0.1, 41.6 + i * 0.1)
    with open(path, "wb") as f:
        f.write(hdr + body)


class DetectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.d = cls.tmp.name

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def path(self, name):
        return os.path.join(self.d, name)

    def write(self, name, data):
        p = self.path(name)
        with open(p, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return p

    def details(self, r):
        return dict(r["details"])

    # ------------------------------------------------------------------ binary
    def test_png_without_extension(self):
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 640, 480, 8, 6, 0, 0, 0)
        r = detector.analyze(self.write("picture", png + b"\0" * 20))
        self.assertEqual(r["kind"], "png")
        self.assertEqual(self.details(r)["d_dimensions"], "640 × 480 px")
        self.assertTrue(r["ext_mismatch"])
        self.assertEqual(r["warnings"][0][0], "warn_no_ext")

    def test_wrong_extension(self):
        r = detector.analyze(self.write("report.jpg", b"%PDF-1.7\n1 0 obj << /Type /Page >> endobj\n"))
        self.assertEqual(r["kind"], "pdf")
        self.assertEqual(r["warnings"][0][0], "warn_ext_mismatch")

    def test_right_extension_no_warning(self):
        r = detector.analyze(self.write("doc.pdf", b"%PDF-1.4\n"))
        self.assertFalse(r["ext_mismatch"])

    def test_geotiff(self):
        p = self.path("raster")
        _tiff_with_geokeys(p)
        r = detector.analyze(p)
        self.assertEqual(r["kind"], "geotiff")
        self.assertEqual(self.details(r)["d_crs"], "EPSG:32638")
        self.assertEqual(r["qgis_layers"][0]["provider"], "gdal")

    def test_shapefile_and_shx(self):
        p = self.path("roads_shp")
        _shp(p)
        r = detector.analyze(p)
        self.assertEqual(r["kind"], "shp")
        self.assertEqual(self.details(r)["d_geometry"], "Point")
        self.assertIn("warn_shp_needs_ext", [w[0] for w in r["warnings"]])

    def test_dbf(self):
        p = self.path("attrs")
        _dbf(p, [("Tbilisi", 504.2), ("Batumi", 64.94)])
        r = detector.analyze(p)
        self.assertEqual(r["kind"], "dbf")
        self.assertEqual(self.details(r)["d_records"], 2)
        self.assertEqual([row[0] for row in r["contents"]["rows"]], ["NAME", "AREA"])

    # ------------------------------------------------------------------ databases
    def test_geopackage(self):
        p = self.path("parcels_db")
        con = sqlite3.connect(p)
        con.execute("PRAGMA application_id = 1196444487")
        con.execute("CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT, srs_id INT)")
        con.execute("CREATE TABLE gpkg_geometry_columns (table_name TEXT, geometry_type_name TEXT)")
        con.execute("CREATE TABLE parcels (fid INTEGER PRIMARY KEY, geom BLOB, owner TEXT)")
        con.execute("INSERT INTO gpkg_contents VALUES ('parcels', 'features', 32638)")
        con.execute("INSERT INTO gpkg_geometry_columns VALUES ('parcels', 'MULTIPOLYGON')")
        con.execute("INSERT INTO parcels (owner) VALUES ('ა'), ('ბ')")
        con.commit()
        con.close()
        r = detector.analyze(p)
        self.assertEqual(r["kind"], "gpkg")
        row = next(x for x in r["contents"]["rows"] if x[0] == "parcels")
        self.assertEqual(row[1], 2)
        self.assertIn("MULTIPOLYGON", row[3])
        self.assertEqual(r["qgis_layers"][0]["uri"], f"{p}|layername=parcels")
        self.assertFalse(os.path.exists(p + "-journal") or os.path.exists(p + "-wal"))

    def test_plain_sqlite(self):
        p = self.path("app_db")
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE users (id INT, name TEXT)")
        con.commit()
        con.close()
        r = detector.analyze(p)
        self.assertEqual(r["kind"], "sqlite")
        self.assertEqual(r["contents"]["rows"][0][0], "users")

    # ------------------------------------------------------------------ archives
    def test_kmz(self):
        p = self.path("trip")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("doc.kml", "<kml><Document><Placemark/></Document></kml>")
        r = detector.analyze(p)
        self.assertEqual(r["kind"], "kmz")
        self.assertTrue(r["qgis_layers"][0]["uri"].startswith("/vsizip/{"))

    def test_zipped_shapefile_missing_parts(self):
        p = self.path("data_zip")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("roads.shp", b"x")
            z.writestr("roads.dbf", b"x")
        r = detector.analyze(p)
        self.assertEqual(r["kind"], "shp_zip")
        warns = [w[0] for w in r["warnings"]]
        self.assertIn("warn_shp_missing", warns)
        self.assertIn("warn_no_prj", warns)

    def test_docx(self):
        p = self.path("letter")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("[Content_Types].xml", "<Types/>")
            z.writestr("word/document.xml", "<w:document/>")
        self.assertEqual(detector.analyze(p)["kind"], "docx")

    # ------------------------------------------------------------------ text
    def test_geojson(self):
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"name": "მცხეთა", "pop": 7940},
             "geometry": {"type": "Point", "coordinates": [44.72, 41.84]}},
            {"type": "Feature", "properties": {"name": "გორი"},
             "geometry": {"type": "Point", "coordinates": [44.11, 41.98]}}]}
        r = detector.analyze(self.write("cities", json.dumps(fc, ensure_ascii=False)))
        self.assertEqual(r["kind"], "geojson")
        d = self.details(r)
        self.assertEqual(d["d_features"], 2)
        self.assertEqual(d["d_encoding"], "UTF-8")
        self.assertEqual(r["contents"]["columns"], ["name", "pop"])

    def test_plain_json(self):
        r = detector.analyze(self.write("config", '[{"a": 1}, {"a": 2}]'))
        self.assertEqual(r["kind"], "json")
        self.assertEqual(self.details(r)["d_items"], 2)

    def test_kml_and_gpx(self):
        kml = '<?xml version="1.0"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Route</name>' \
              + "<Placemark/>" * 3 + "</Document></kml>"
        r = detector.analyze(self.write("route", kml))
        self.assertEqual(r["kind"], "kml")
        self.assertEqual(self.details(r)["d_placemarks"], "3")
        gpx = '<?xml version="1.0"?><gpx creator="Garmin"><trk><trkseg>' + '<trkpt lat="1" lon="2"/>' * 4 + "</trkseg></trk></gpx>"
        r = detector.analyze(self.write("track", gpx))
        self.assertEqual(r["kind"], "gpx")
        self.assertEqual(self.details(r)["d_track_points"], "4")

    def test_csv_with_coordinates(self):
        r = detector.analyze(self.write("points", "id;name;lat;lon\n1;A;41.7;44.8\n2;B;42.2;43.1\n3;C;41.6;41.6\n"))
        self.assertEqual(r["kind"], "csv_points")
        self.assertIn("xField=lon", r["qgis_layers"][0]["uri"])
        self.assertIn("delimiter=;", r["qgis_layers"][0]["uri"])

    def test_csv_with_wkt(self):
        r = detector.analyze(self.write("geoms", 'id,wkt\n1,"POINT (44 41)"\n2,"POINT (45 42)"\n3,"POINT (43 41)"\n'))
        self.assertEqual(r["kind"], "csv_points")
        self.assertIn("wktField=wkt", r["qgis_layers"][0]["uri"])

    def test_plain_csv(self):
        r = detector.analyze(self.write("table", "a,b,c\n1,2,3\n4,5,6\n7,8,9\n"))
        self.assertEqual(r["kind"], "csv")
        self.assertEqual(r["qgis_layers"], [])

    def test_prj(self):
        wkt = 'PROJCS["WGS 84 / UTM zone 38N",GEOGCS["WGS 84"],AUTHORITY["EPSG","32638"]]'
        r = detector.analyze(self.write("proj", wkt))
        self.assertEqual(r["kind"], "prj")
        self.assertEqual(self.details(r)["d_epsg"], "EPSG:32638")

    def test_nmea(self):
        r = detector.analyze(self.write("gpslog", "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\n"
                                                  "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A\n"))
        self.assertEqual(r["kind"], "nmea")

    def test_script_warns(self):
        r = detector.analyze(self.write("run", "#!/bin/bash\necho hi\n"))
        self.assertEqual(r["kind"], "script")
        self.assertIn("warn_executable", [w[0] for w in r["warnings"]])

    def test_qgis_project_and_style(self):
        r = detector.analyze(self.write("proj_x", '<!DOCTYPE qgis PUBLIC "x" "y">\n<qgis projectname="" version="3.44.0">'
                                                  "<projectlayers><maplayer/></projectlayers></qgis>"))
        self.assertEqual(r["kind"], "qgs")
        r = detector.analyze(self.write("style_x", '<qgis version="3.44.0" styleCategories="AllStyleCategories">'
                                                   "<renderer-v2/></qgis>"))
        self.assertEqual(r["kind"], "qml")

    def test_georgian_text(self):
        r = detector.analyze(self.write("note", "გამარჯობა, ეს უბრალო ტექსტია.\nმეორე ხაზი.\n"))
        self.assertEqual(r["kind"], "text")
        self.assertEqual(self.details(r)["d_encoding"], "UTF-8")

    def test_empty_and_random(self):
        self.assertEqual(detector.analyze(self.write("empty", b""))["kind"], "empty")
        r = detector.analyze(self.write("random", os.urandom(64 * 1024)))
        self.assertEqual(r["kind"], "unknown")
        self.assertIn("warn_high_entropy", [w[0] for w in r["warnings"]])

    def test_damaged_file_does_not_crash(self):
        r = detector.analyze(self.write("broken", b"PK\x03\x04" + b"\0" * 50))
        self.assertEqual(r["kind"], "zip")
        self.assertIn("warn_parse_failed", [w[0] for w in r["warnings"]])

    def test_report_in_both_languages(self):
        r = detector.analyze(self.write("cities2", '{"type":"Point","coordinates":[44,41]}'))
        for lang in ("ka", "en"):
            i18n.set_lang(lang)
            text = detector.report_text(r, i18n.t)
            self.assertIn("GeoJSON", text)
        i18n.set_lang("ka")


class TranslationTests(unittest.TestCase):
    def test_every_key_used_by_detector_is_translated(self):
        import re
        with open(detector.__file__, encoding="utf-8") as f:
            src = f.read()
        keys = set(re.findall(r'"((?:d|warn|c|col)_[a-z_]+)"', src))
        keys |= {"cat_" + c for c in detector.CATEGORIES}
        missing = sorted(k for k in keys if k not in i18n.STRINGS)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
