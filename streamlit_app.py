
import base64
import json
import math
import xml.etree.ElementTree as ET

import pydeck as pdk
import requests
import streamlit as st


st.set_page_config(page_title="Metsänostaja", layout="wide")
st.title("Metsänostaja")
st.caption("Kiinteistörajat ja Maanmittauslaitoksen kartat")

MML_KIINTEISTOT = (
    "https://avoin-paikkatieto.maanmittauslaitos.fi/"
    "kiinteisto-avoin/simple-features/v3/"
    "collections/PalstanSijaintitiedot/items"
)

MML_WMTS = (
    "https://avoin-karttakuva.maanmittauslaitos.fi/"
    "avoin/wmts/1.0.0"
)

METSÄKESKUS_WFS = (
    "https://avoin.metsakeskus.fi/"
    "rajapinnat/v2/stand/ows"
)

KARTAT = {
    "Maastokartta": ("maastokartta", "png"),
    "Ilmakuva": ("ortokuva", "jpg"),
    "Taustakartta": ("taustakartta", "png"),
}


def muotoile_tunnus(teksti):
    osat = teksti.strip().split("-")

    if len(osat) != 4 or not all(osa.isdigit() for osa in osat):
        raise ValueError("Anna tunnus muodossa 174-401-3-6.")

    return (
        f"{int(osat[0]):03d}"
        f"{int(osat[1]):03d}"
        f"{int(osat[2]):04d}"
        f"{int(osat[3]):04d}"
    )


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
    if not isinstance(koordinaatit, (list, tuple)):
        return []

    if not koordinaatit:
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

    y = (
        1.0
        - math.asinh(math.tan(math.radians(lat))) / math.pi
    ) / 2.0 * maara

    return x, y


def slippy_kulma(x, y, taso):
    maara = 2**taso

    lon = x / maara * 360.0 - 180.0

    lat = math.degrees(
        math.atan(
            math.sinh(math.pi * (1 - 2 * y / maara))
        )
    )

    return lon, lat


def valitse_taso(pisteet):
    lonit = [p[0] for p in pisteet]
    latit = [p[1] for p in pisteet]

    for taso in range(16, 5, -1):
        vasen, yla = slippy_xy(
            min(lonit), max(latit), taso
        )

        oikea, ala = slippy_xy(
            max(lonit), min(latit), taso
        )

        if oikea - vasen <= 0.9 and ala - yla <= 0.9:
            return taso

    return 6


@st.cache_data(ttl=86400, show_spinner=False)
def hae_karttatiili(karttataso, tarkkuus, sarake, rivi):
    nimi, paate = KARTAT[karttataso]

    osoite = (
        f"{MML_WMTS}/{nimi}/default/"
        f"WGS84_Pseudo-Mercator/"
        f"{tarkkuus}/{rivi}/{sarake}.{paate}"
    )

    vastaus = requests.get(
        osoite,
        params={
            "api-key": st.secrets["MML_API_KEY"]
        },
        timeout=25,
    )

    vastaus.raise_for_status()

    sisalto = vastaus.headers.get("Content-Type", "")

    if not sisalto.startswith("image/"):
        raise ValueError(
            "Karttapalvelu ei palauttanut kuvaa."
        )

    mime = (
        "image/jpeg"
        if paate == "jpg"
        else "image/png"
    )

    kuva = base64.b64encode(
        vastaus.content
    ).decode("ascii")

    return f"data:{mime};base64,{kuva}"


def muodosta_kartta(kiinteisto, karttataso):
    palstat = kiinteisto.get("features", [])

    pisteet = []

    for palsta in palstat:
        geometria = palsta.get("geometry") or {}

        pisteet.extend(
            kaikki_pisteet(
                geometria.get("coordinates", [])
            )
        )

    if not pisteet:
        raise ValueError(
            "Kiinteistön palstojen geometria puuttuu."
        )

    kesk_lon = (
        min(p[0] for p in pisteet)
        + max(p[0] for p in pisteet)
    ) / 2

    kesk_lat = (
        min(p[1] for p in pisteet)
        + max(p[1] for p in pisteet)
    ) / 2

    tarkkuus = valitse_taso(pisteet)

    keski_x, keski_y = slippy_xy(
        kesk_lon, kesk_lat, tarkkuus
    )

    keski_sarake = math.floor(keski_x)
    keski_rivi = math.floor(keski_y)

    tasot = []

    for rivi in range(
        keski_rivi - 1, keski_rivi + 2
    ):
        for sarake in range(
            keski_sarake - 1, keski_sarake + 2
        ):
            if not (
                0 <= rivi < 2**tarkkuus
                and 0 <= sarake < 2**tarkkuus
            ):
                continue

            kuva = hae_karttatiili(
                karttataso,
                tarkkuus,
                sarake,
                rivi,
            )

            vasen, yla = slippy_kulma(
                sarake, rivi, tarkkuus
            )

            oikea, ala = slippy_kulma(
                sarake + 1,
                rivi + 1,
                tarkkuus,
            )

            tasot.append(
                pdk.Layer(
                    "BitmapLayer",
                    image=kuva,
                    bounds=[
                        vasen, ala, oikea, yla
                    ],
                    opacity=1,
                )
            )

    tasot.append(
        pdk.Layer(
            "GeoJsonLayer",
            data=kiinteisto,
            filled=True,
            stroked=True,
            get_fill_color=[255, 130, 0, 25],
            get_line_color=[255, 90, 0, 255],
            line_width_min_pixels=4,
            pickable=True,
        )
    )

    return pdk.Deck(
        layers=tasot,
        initial_view_state=pdk.ViewState(
            longitude=kesk_lon,
            latitude=kesk_lat,
            zoom=tarkkuus - 0.9,
        ),
        map_style=None,
    )


@st.cache_data(ttl=86400, show_spinner=False)
def metsakeskuksen_tasot():
    vastaus = requests.get(
        METSÄKESKUS_WFS,
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetCapabilities",
        },
        timeout=30,
    )

    vastaus.raise_for_status()

    root = ET.fromstring(vastaus.content)

    namespace = (
        "{http://www.opengis.net/wfs/2.0}"
    )

    return [
        alkio.text
        for alkio in root.findall(
            f".//{namespace}FeatureType/"
            f"{namespace}Name"
        )
        if alkio.text
    ]


try:
    avain = st.secrets["MML_API_KEY"]

    if not avain:
        st.error(
            "MML_API_KEY on tyhjä."
        )
        st.stop()

except (KeyError, FileNotFoundError):
    st.error(
        "Lisää MML_API_KEY Streamlitin "
        "Secrets-asetuksiin."
    )
    st.stop()


st.subheader("Kiinteistöhaku")

syote = st.text_input(
    "Syötä kiinteistötunnus",
    value="174-401-3-6",
)

if st.button(
    "Hae kiinteistön tiedot",
    type="primary",
):
    try:
        tunnus = muotoile_tunnus(syote)

        with st.spinner(
            "Haetaan kiinteistön palstoja..."
        ):
            tulos = hae_kiinteisto(tunnus)

        st.session_state["kiinteisto"] = tulos
        st.session_state["haettu_tunnus"] = syote

    except ValueError as virhe:
        st.error(str(virhe))

    except requests.RequestException:
        st.error(
            "Kiinteistöhaku epäonnistui. "
            "Tarkista tunnus ja rajapinnan toiminta."
        )


if "kiinteisto" in st.session_state:
    tiedot = st.session_state["kiinteisto"]
    palstat = tiedot.get("features", [])

    if palstat:
        st.success("Kiinteistö löytyi!")

        st.write(
            "Kiinteistötunnus: "
            f"**{st.session_state['haettu_tunnus']}**"
        )

        st.write(
            f"Palstojen määrä: **{len(palstat)}**"
        )

        karttataso = st.radio(
            "Karttapohja",
            [
                "Maastokartta",
                "Ilmakuva",
                "Taustakartta",
            ],
            horizontal=True,
        )

        st.subheader(
            "Kiinteistön palstat kartalla"
        )

        try:
            with st.spinner(
                "Ladataan Maanmittauslaitoksen karttaa..."
            ):
                kartta = muodosta_kartta(
                    tiedot, karttataso
                )

            st.pydeck_chart(
                kartta,
                use_container_width=True,
            )

            st.caption(
                "Kartta-aineisto © Maanmittauslaitos "
                "(CC BY 4.0)."
            )

        except (
            requests.RequestException,
            ValueError,
        ):
            st.error(
                "Karttapohjan lataaminen epäonnistui."
            )

        for i, palsta in enumerate(
            palstat, 1
        ):
            with st.expander(
                f"Palsta {i}: tiedot"
            ):
                st.json(
                    palsta.get("properties", {})
                )

        st.download_button(
            "Lataa kiinteistön rajat (GeoJSON)",
            json.dumps(
                tiedot,
                ensure_ascii=False,
            ),
            file_name="kiinteisto.geojson",
            mime="application/geo+json",
        )

    else:
        st.warning(
            "Kiinteistötunnuksella ei löytynyt palstoja."
        )


st.divider()

st.subheader(
    "Metsäkeskuksen metsävaratiedot"
)

st.write(
    "Testataan metsävarakuvioiden WFS-palvelua."
)

if st.button(
    "Hae metsävaratietojen tasot"
):
    try:
        with st.spinner(
            "Haetaan metsävarakuvioiden tasoja..."
        ):
            tasot = metsakeskuksen_tasot()

        if tasot:
            st.success(
                f"Yhteys toimii. "
                f"Tasoja löytyi {len(tasot)}."
            )

            for taso in tasot:
                st.write(taso)

        else:
            st.warning(
                "Rajapinta vastasi, mutta "
                "tasoja ei löytynyt."
            )

    except (
        requests.RequestException,
        ET.ParseError,
    ):
        st.error(
            "Metsäkeskuksen rajapinnan "
            "tietoja ei saatu ladattua."
        )
