import base64
import json
import math
import xml.etree.ElementTree as ET

import requests
import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(page_title="Metsänostaja", layout="wide")
st.title("Metsänostaja")
st.caption("Metsätilojen analysointisovellus")

MML_KIINTEISTOT = (
    "https://avoin-paikkatieto.maanmittauslaitos.fi/"
    "kiinteisto-avoin/simple-features/v3/"
    "collections/PalstanSijaintitiedot/items"
)
MML_WMTS = "https://avoin-karttakuva.maanmittauslaitos.fi/avoin/wmts/1.0.0"
METSÄKESKUS_WFS = "https://avoin.metsakeskus.fi/rajapinnat/v2/stand/ows"

KARTAT = {
    "Maastokartta": ("maastokartta", "png"),
    "Ilmakuva": ("ortokuva", "jpg"),
    "Taustakartta": ("taustakartta", "png"),
}


def muotoile_tunnus(teksti):
    osat = teksti.strip().split("-")
    if len(osat) != 4 or not all(osa.isdigit() for osa in osat):
        raise ValueError("Anna tunnus muodossa 174-401-3-6.")
    return f"{int(osat[0]):03d}{int(osat[1]):03d}{int(osat[2]):04d}{int(osat[3]):04d}"


@st.cache_data(ttl=3600, show_spinner=False)
def hae_kiinteisto(tunnus):
    vastaus = requests.get(
        MML_KIINTEISTOT,
        params={
            "kiinteistotunnus": tunnus,
            "api-key": st.secrets["MML_API_KEY"],
            "limit": 100,
        },
        timeout=30,
    )
    vastaus.raise_for_status()
    return vastaus.json()


def kaikki_pisteet(koordinaatit):
    if not isinstance(koordinaatit, (list, tuple)) or not koordinaatit:
        return []
    if isinstance(koordinaatit[0], (int, float)):
        return [koordinaatit[:2]]
    tulos = []
    for osa in koordinaatit:
        tulos.extend(kaikki_pisteet(osa))
    return tulos


def slippy_xy(lon, lat, taso):
    lat = min(85.0, max(-85.0, lat))
    maara = 2**taso
    x = (lon + 180.0) / 360.0 * maara
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * maara
    return x, y


def slippy_kulma(x, y, taso):
    maara = 2**taso
    lon = x / maara * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / maara))))
    return lon, lat


def valitse_taso(pisteet):
    lonit = [p[0] for p in pisteet]
    latit = [p[1] for p in pisteet]
    for taso in range(16, 5, -1):
        vasen, yla = slippy_xy(min(lonit), max(latit), taso)
        oikea, ala = slippy_xy(max(lonit), min(latit), taso)
        if oikea - vasen <= 0.9 and ala - yla <= 0.9:
            return taso
    return 6


@st.cache_data(ttl=86400, show_spinner=False)
def hae_karttatiili(karttataso, tarkkuus, sarake, rivi):
    nimi, paate = KARTAT[karttataso]
    osoite = (
        f"{MML_WMTS}/{nimi}/default/WGS84_Pseudo-Mercator/"
        f"{tarkkuus}/{rivi}/{sarake}.{paate}"
    )
    vastaus = requests.get(
        osoite,
        params={"api-key": st.secrets["MML_API_KEY"]},
        timeout=25,
    )
    vastaus.raise_for_status()
    if not vastaus.headers.get("Content-Type", "").startswith("image/"):
        raise ValueError("MML-karttapalvelu ei palauttanut kuvaa.")
    mime = "image/jpeg" if paate == "jpg" else "image/png"
    kuva = base64.b64encode(vastaus.content).decode("ascii")
    return f"data:{mime};base64,{kuva}"


def koordinaatit_mercatoriin(lon, lat):
    # Web Mercator (EPSG:3857), WFS:n rajaus metreinä.
    lat = max(-85.0511, min(85.0511, lat))
    r = 6378137.0
    x = r * math.radians(lon)
    y = r * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def kuvaile_polygonit(geometria):
    # Palauttaa kunkin polygonin ulkorenkaan GeoJSONista.
    if not geometria:
        return []
    t = geometria.get("type")
    koord = geometria.get("coordinates") or []
    if t == "Polygon" and koord:
        return [koord[0]]
    if t == "MultiPolygon":
        return [pol[0] for pol in koord if pol]
    return []


def piste_sisalla(piste, rengas):
    x, y = piste
    sisalla = False
    for i in range(len(rengas)):
        ax, ay = rengas[i - 1][:2]
        bx, by = rengas[i][:2]
        if (ay > y) != (by > y):
            leikkaus_x = ax + (y - ay) * (bx - ax) / (by - ay)
            if x < leikkaus_x:
                sisalla = not sisalla
    return sisalla


def suuntamerkki(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def janat_leikkaavat(a, b, c, d):
    # Rajojen leikkaus; riittävä metsäkuvioiden esikatselusuodatukseen.
    eps = 1e-12
    o1, o2 = suuntamerkki(a, b, c), suuntamerkki(a, b, d)
    o3, o4 = suuntamerkki(c, d, a), suuntamerkki(c, d, b)
    if o1 * o2 < -eps and o3 * o4 < -eps:
        return True
    for p, q, r, t in ((a, b, c, o1), (a, b, d, o2), (c, d, a, o3), (c, d, b, o4)):
        if abs(t) < eps and min(p[0], q[0])-eps <= r[0] <= max(p[0], q[0])+eps and min(p[1], q[1])-eps <= r[1] <= max(p[1], q[1])+eps:
            return True
    return False


def renkaat_leikkaavat(a, b):
    if not a or not b:
        return False
    if piste_sisalla(a[0], b) or piste_sisalla(b[0], a):
        return True
    return any(
        janat_leikkaavat(a[i-1], a[i], b[j-1], b[j])
        for i in range(len(a)) for j in range(len(b))
    )


def geometria_osuu_kiinteistoon(kuvio, palstat):
    kuviorenkaat = kuvaile_polygonit(kuvio)
    palstarenkaat = [
        rengas
        for palsta in palstat
        for rengas in kuvaile_polygonit(palsta.get("geometry"))
    ]
    return any(
        renkaat_leikkaavat(kr, pr)
        for kr in kuviorenkaat for pr in palstarenkaat
    )


@st.cache_data(ttl=3600, show_spinner=False)
def hae_metsakuviot(kiinteiston_json):
    """WFS:n aluerajaushaku palstoittain, GeoJSON-muodossa.

    WFS:stä haetaan palstan ympäröivät kuviot ja jätetään näytölle
    vain palstan kanssa leikkaavat kuviot. Tämä ei vielä laske
    kuvioiden täsmällistä pinta-alaosuutta kiinteistön sisällä.
    """
    palstat = kiinteiston_json.get("features", [])
    kaikki = {}
    rajoitettu = False

    for palsta in palstat:
        geo = palsta.get("geometry") or {}
        pisteet = kaikki_pisteet(geo.get("coordinates", []))
        if not pisteet:
            continue
        xy = [koordinaatit_mercatoriin(p[0], p[1]) for p in pisteet]
        xmin = min(x for x, _ in xy) - 20
        ymin = min(y for _, y in xy) - 20
        xmax = max(x for x, _ in xy) + 20
        ymax = max(y for _, y in xy) + 20
        bbox = f"{xmin},{ymin},{xmax},{ymax},EPSG:3857"
        vastaus = requests.get(
            METSÄKESKUS_WFS,
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": "v2:stand",
                "bbox": bbox,
                "srsName": "EPSG:4326",
                "outputFormat": "application/json",
                "count": 1000,
            },
            timeout=60,
        )
        vastaus.raise_for_status()
        try:
            aineisto = vastaus.json()
        except ValueError as exc:
            virhe = vastaus.text[:400]
            raise ValueError(f"Metsäkeskus ei palauttanut GeoJSONia: {virhe}") from exc
        if aineisto.get("type") != "FeatureCollection":
            raise ValueError(f"Odottamaton vastaus Metsäkeskukselta: {str(aineisto)[:400]}")
        features = aineisto.get("features", [])
        if len(features) >= 1000:
            rajoitettu = True
        for f in features:
            tunnus = f.get("id") or json.dumps(f.get("geometry", {}), sort_keys=True)
            kaikki[str(tunnus)] = f

    # Rajataan näyttöön vain palstan kanssa päällekkäiset kuviot.
    # Polygonien reikien käsittelyä ei vielä ole täydennetty.
    valitut = [
        f for f in kaikki.values()
        if geometria_osuu_kiinteistoon(f.get("geometry"), palstat)
    ]
    return {"type": "FeatureCollection", "features": valitut}, rajoitettu


def muodosta_kartta_html(kiinteisto, karttataso, metsakuviot=None):
    pisteet = []
    for palsta in kiinteisto.get("features", []):
        geometria = palsta.get("geometry") or {}
        pisteet.extend(kaikki_pisteet(geometria.get("coordinates", [])))
    if not pisteet:
        raise ValueError("Palstojen sijaintitiedot puuttuvat.")

    kesk_lon = (min(p[0] for p in pisteet) + max(p[0] for p in pisteet)) / 2
    kesk_lat = (min(p[1] for p in pisteet) + max(p[1] for p in pisteet)) / 2
    tarkkuus = valitse_taso(pisteet)
    keski_x, keski_y = slippy_xy(kesk_lon, kesk_lat, tarkkuus)
    keski_sarake, keski_rivi = math.floor(keski_x), math.floor(keski_y)

    karttatiilet = []
    for rivi in range(keski_rivi - 1, keski_rivi + 2):
        for sarake in range(keski_sarake - 1, keski_sarake + 2):
            if not (0 <= rivi < 2**tarkkuus and 0 <= sarake < 2**tarkkuus):
                continue
            kuva = hae_karttatiili(karttataso, tarkkuus, sarake, rivi)
            vasen, yla = slippy_kulma(sarake, rivi, tarkkuus)
            oikea, ala = slippy_kulma(sarake + 1, rivi + 1, tarkkuus)
            karttatiilet.append({
                "image": kuva,
                "bounds": [[ala, vasen], [yla, oikea]],
            })

    # Vain julkiset palstarajat ja valmiiksi ladatut kuvat selaimeen,
    # API-avain pysyy Streamlit-palvelimella.
    rajat_json = json.dumps(
        {"type": "FeatureCollection", "features": kiinteisto.get("features", [])},
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    tiilet_json = json.dumps(karttatiilet).replace("<", "\\u003c")
    kuviot_json = json.dumps(
        metsakuviot or {"type": "FeatureCollection", "features": []},
        ensure_ascii=False,
    ).replace("<", "\\u003c")

    html = """<!DOCTYPE html>
<html lang="fi">
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html, body, #kartta {height:100%;width:100%;margin:0;padding:0;}</style>
</head>
<body>
<div id="kartta"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const kartta = L.map('kartta', {zoomControl: true});
const tiilet = __KARTTATIILIT__;
const geojson = __KIINTEISTO__;
const kuviotGeoJSON = __METSÄKUVIOT__;
for (const tiili of tiilet) {
    L.imageOverlay(tiili.image, tiili.bounds, {interactive: false}).addTo(kartta);
}
const kuviot = L.geoJSON(kuviotGeoJSON, {
    style: {color:'#1285db', weight:2, fillColor:'#218cf2', fillOpacity:0.13}
}).addTo(kartta);
const palstat = L.geoJSON(geojson, {
    style: {color:'#ff6500', weight:4, fillColor:'#ff6500', fillOpacity:0.08}
}).addTo(kartta);
if (palstat.getBounds().isValid()) {
    kartta.fitBounds(palstat.getBounds(), {padding: [50,50], maxZoom: 16});
} else {
    kartta.setView([__KESKI_LAT__, __KESKI_LON__], 10);
}
L.control.scale({imperial:false}).addTo(kartta);
L.control.attribution({prefix:false}).addTo(kartta)
    .addAttribution('Kartta-aineisto © Maanmittauslaitos');
</script>
</body></html>"""
    html = html.replace("__KARTTATIILIT__", tiilet_json)
    html = html.replace("__KIINTEISTO__", rajat_json)
    html = html.replace("__METSÄKUVIOT__", kuviot_json)
    html = html.replace("__KESKI_LAT__", json.dumps(kesk_lat))
    html = html.replace("__KESKI_LON__", json.dumps(kesk_lon))
    return html


@st.cache_data(ttl=86400, show_spinner=False)
def metsakeskuksen_tasot():
    vastaus = requests.get(
        METSÄKESKUS_WFS,
        params={"service": "WFS", "version": "2.0.0", "request": "GetCapabilities"},
        timeout=30,
    )
    vastaus.raise_for_status()
    root = ET.fromstring(vastaus.content)
    ns = "{http://www.opengis.net/wfs/2.0}"
    return [
        nimi.text
        for nimi in root.findall(f".//{ns}FeatureType/{ns}Name")
        if nimi.text
    ]


try:
    if not st.secrets["MML_API_KEY"]:
        st.error("MML_API_KEY on tyhjä.")
        st.stop()
except (KeyError, FileNotFoundError):
    st.error("Lisää MML_API_KEY Streamlitin Secrets-asetuksiin.")
    st.stop()

st.subheader("Kiinteistöhaku")
syote = st.text_input("Kiinteistötunnus", value="174-401-3-6")

if st.button("Hae kiinteistön tiedot", type="primary"):
    try:
        tunnus = muotoile_tunnus(syote)
        with st.spinner("Haetaan kiinteistön palstoja..."):
            st.session_state["kiinteisto"] = hae_kiinteisto(tunnus)
            st.session_state["haettu_tunnus"] = syote
            st.session_state.pop("metsakuviot", None)
            st.session_state.pop("rajattu_tulos", None)
    except ValueError as virhe:
        st.error(str(virhe))
    except requests.RequestException:
        st.error("Kiinteistön haku epäonnistui. Tarkista tunnus.")

if "kiinteisto" in st.session_state:
    tiedot = st.session_state["kiinteisto"]
    palstat = tiedot.get("features", [])
    if palstat:
        st.success("Kiinteistö löytyi!")
        st.write(f"Kiinteistötunnus: **{st.session_state['haettu_tunnus']}**")
        st.write(f"Palstojen määrä: **{len(palstat)}**")

        karttataso = st.radio(
            "Karttapohja", list(KARTAT), horizontal=True
        )
        st.subheader("Kiinteistön palstat kartalla")
        if st.button("Hae metsäkuviot tälle kiinteistölle"):
            try:
                with st.spinner("Haetaan Metsäkeskuksen metsävarakuvioita..."):
                    kuviot, rajattu = hae_metsakuviot(tiedot)
                st.session_state["metsakuviot"] = kuviot
                st.session_state["rajattu_tulos"] = rajattu
            except (requests.RequestException, ValueError) as virhe:
                st.error(f"Metsäkeskuksen haku epäonnistui: {virhe}")

        metsakuviot = st.session_state.get("metsakuviot")
        if metsakuviot is not None:
            st.write(f"Kiinteistön kanssa leikkaavia metsäkuvioita: **{len(metsakuviot.get('features', []))}**")
            if st.session_state.get("rajattu_tulos"):
                st.warning("Metsäkeskuksen kysely saavutti 1000 kuvion hakurajan. Tulos ei välttämättä sisällä kaikkia kuvioita.")
            st.caption("Siniset rajat = metsäkuviot, oranssit = kiinteistö. Kuvio voi ulottua kiinteistön ulkopuolelle.")
        try:
            with st.spinner("Ladataan Maanmittauslaitoksen karttaa..."):
                html = muodosta_kartta_html(tiedot, karttataso, metsakuviot)
            components.html(html, height=600, scrolling=False)
            st.caption("Kartta-aineisto © Maanmittauslaitos. Kartta ladataan rajatulta alueelta.")
        except (requests.RequestException, ValueError):
            st.error("Karttapohjan lataaminen epäonnistui.")

        if metsakuviot is not None:
            with st.expander("Metsäkuvioiden tiedot"):
                for i, kuvio in enumerate(metsakuviot.get("features", []), 1):
                    st.markdown(f"**Kuvio {i}**")
                    st.json(kuvio.get("properties", {}))
            st.download_button(
                "Lataa metsäkuviot (GeoJSON)",
                json.dumps(metsakuviot, ensure_ascii=False),
                file_name="metsakuviot.geojson",
                mime="application/geo+json",
            )
            st.info("Tämä versio näyttää kuviot ja niiden alkuperäiset tiedot. Puustotilavuutta ei vielä summata: ensin on laskettava kunkin kuvion osuus kiinteistöstä ja tarkistettava tietokenttien sisältö.")

        for i, palsta in enumerate(palstat, 1):
            with st.expander(f"Palsta {i}: tiedot"):
                st.json(palsta.get("properties", {}))
        st.download_button(
            "Lataa kiinteistön rajat (GeoJSON)",
            json.dumps(tiedot, ensure_ascii=False),
            file_name="kiinteisto.geojson",
            mime="application/geo+json",
        )
    else:
        st.warning("Kiinteistötunnuksella ei löytynyt palstoja.")

st.divider()
st.subheader("Metsäkeskuksen metsävaratiedot")
if st.button("Hae metsävaratietojen tasot"):
    try:
        with st.spinner("Haetaan WFS-palvelun tasoja..."):
            tasot = metsakeskuksen_tasot()
        if tasot:
            st.success(f"Yhteys toimii. Tasoja löytyi {len(tasot)}.")
            for taso in tasot:
                st.write(taso)
        else:
            st.warning("Rajapinta vastasi, mutta tasoja ei löytynyt.")
    except (requests.RequestException, ET.ParseError):
        st.error("Metsäkeskuksen rajapinnan haku epäonnistui.")
