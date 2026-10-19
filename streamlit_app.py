
import streamlit as st
import requests
import json
import math
import pydeck as pdk

st.set_page_config(page_title="Metsänostaja", layout="wide")

st.title("Metsänostaja")
st.write("Metsätilojen analysointisovellus")


def muotoile_tunnus(tunnus):
    osat = tunnus.strip().split("-")

    if len(osat) != 4 or not all(x.isdigit() for x in osat):
        raise ValueError("Virheellinen kiinteistötunnus.")

    return (
        f"{int(osat[0]):03d}"
        f"{int(osat[1]):03d}"
        f"{int(osat[2]):04d}"
        f"{int(osat[3]):04d}"
    )


def hae_kiinteisto(tunnus):
    url = (
        "https://avoin-paikkatieto.maanmittauslaitos.fi/"
        "kiinteisto-avoin/simple-features/v3/"
        "collections/PalstanSijaintitiedot/items"
    )

    vastaus = requests.get(
        url,
        params={
            "kiinteistotunnus": tunnus,
            "api-key": st.secrets["MML_API_KEY"],
            "limit": 100,
        },
        timeout=30,
    )
    vastaus.raise_for_status()
    return vastaus.json()


def hae_koordinaatit(data):
    if not isinstance(data, list) or not data:
        return []

    if isinstance(data[0], (int, float)):
        return [data[:2]]

    tulos = []
    for osa in data:
        tulos.extend(hae_koordinaatit(osa))
    return tulos


kiinteistotunnus = st.text_input(
    "Syötä kiinteistötunnus",
    placeholder="174-401-3-6"
)

if st.button("Hae metsätilan tiedot"):
    try:
        tunnus = muotoile_tunnus(kiinteistotunnus)

        with st.spinner("Haetaan kiinteistötietoja..."):
            tiedot = hae_kiinteisto(tunnus)

        palstat = tiedot.get("features", [])

        if palstat:
            st.success("Kiinteistö löytyi!")
            st.write(f"Kiinteistötunnus: {kiinteistotunnus}")
            st.write(f"Palstojen määrä: {len(palstat)}")

            koordinaatit = []

            for palsta in palstat:
                geometria = palsta.get("geometry")
                if geometria:
                    koordinaatit.extend(
                        hae_koordinaatit(
                            geometria.get("coordinates", [])
                        )
                    )

            if koordinaatit:
                lon = [p[0] for p in koordinaatit]
                lat = [p[1] for p in koordinaatit]

                keski_lon = (min(lon) + max(lon)) / 2
                keski_lat = (min(lat) + max(lat)) / 2

                leveys = (
                    (max(lon) - min(lon))
                    * math.cos(math.radians(keski_lat))
                )
                korkeus = max(lat) - min(lat)
                laajuus = max(leveys, korkeus, 0.0001)

                zoom = max(
                    3,
                    min(16, math.log2(360 / laajuus) - 3)
                )

                st.subheader("Kiinteistön palstat kartalla")

                kartta = pdk.Deck(
                    map_style=None,
                    initial_view_state=pdk.ViewState(
                        latitude=keski_lat,
                        longitude=keski_lon,
                        zoom=zoom,
                    ),
                    layers=[
                        pdk.Layer(
                            "GeoJsonLayer",
                            data=tiedot,
                            filled=True,
                            stroked=True,
                            get_fill_color=[30, 160, 80, 70],
                            get_line_color=[255, 100, 0],
                            line_width_min_pixels=3,
                            pickable=True,
                        )
                    ],
                )

                st.pydeck_chart(kartta)
            else:
                st.warning("Palstojen geometriaa ei löytynyt.")

            for numero, palsta in enumerate(palstat, 1):
                with st.expander(f"Palsta {numero}"):
                    st.json(palsta.get("properties", {}))

            st.download_button(
                "Lataa kiinteistön paikkatiedot",
                data=json.dumps(tiedot, ensure_ascii=False),
                file_name="kiinteisto.geojson",
                mime="application/geo+json",
            )

        else:
            st.warning("Kiinteistöä ei löytynyt.")

    except ValueError as virhe:
        st.error(str(virhe))
    except requests.exceptions.RequestException as virhe:
        st.error(f"Tietojen haku epäonnistui: {virhe}")
    except KeyError:
        st.error("API-avain puuttuu Streamlitin asetuksista.")

