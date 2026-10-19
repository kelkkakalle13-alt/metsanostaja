import streamlit as st

st.set_page_config(page_title="Metsänostaja", layout="wide")

st.title("Metsänostaja")
st.write("Metsätilojen analysointisovellus")

kiinteistotunnus = st.text_input("Syötä kiinteistötunnus")

if st.button("Hae metsätilan tiedot"):
    if kiinteistotunnus:
        st.info(f"Haettava kiinteistö: {kiinteistotunnus}")
    else:
        st.warning("Syötä kiinteistötunnus.")
