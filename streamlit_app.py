
import streamlit as st
import requests
import json

st.set_page_config(page_title="Metsänostaja", layout="wide")

st.title("Metsänostaja")
st.write("Metsätilojen analysointisovellus")

def muotoile_tunnus(tunnus):
    osat = tunnus.strip().split("-")

    if len(osat) != 4 or not all(osa.isdigit() for osa in osat):
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

    params = {
        "kiinteistotunnus": tunnus,
        "api-key": st.secrets["MML_API_KEY"],
        "limit": 100,
    }

    vastaus = requests.get(url, params=params, timeout=30)
    vastaus.raise_for_status()

    return vastaus.json()

kiinteistotunnus = st.text_input(
    "Syötä kiinteistötunnus",
    placeholder="286-416-1-1"
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

            for numero, palsta in enumerate(palstat, start=1):
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
    except requests.exceptions.RequestException:
        st.error("Tietojen haku epäonnistui. Tarkista API-avain.")
    except KeyError:
        st.error("API-avain puuttuu Streamlitin Secrets-asetuksista.")
