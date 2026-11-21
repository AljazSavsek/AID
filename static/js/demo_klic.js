/* Demo klic (scenarij 1: padec z lestve, Ljubljana – Šiška).
 * segmenti: kar bi vrnil streaming STT (končni segmenti).
 * ai: kaj bi AI poslušalec vrnil po prejemu segmenta (izhod po shemi iz specifikacije, razdelek 5).
 * Vsak predlog ima citat iz prepisa in zaupanje; ničesar, kar ni izrečeno, se ne predlaga.
 */
window.AID_DEMO_KLIC = {
  klicatelj: "+386 41 555 238",
  omrezje: "Ljubljana – Šiška (bazna postaja)",
  segmenti: [
    { id: "s1", govorec: "dispecer", od_s: 0.0, do_s: 2.6, besedilo: "Nujna medicinska pomoč, dober dan. Kaj se je zgodilo?" },
    { id: "s2", govorec: "klicatelj", od_s: 3.1, do_s: 7.9, besedilo: "Dober dan, moj oče je padel z lestve, leži na tleh in se ne odziva!" },
    { id: "s3", govorec: "dispecer", od_s: 8.6, do_s: 10.4, besedilo: "Razumem. Povejte mi točen naslov, kjer ste." },
    { id: "s4", govorec: "klicatelj", od_s: 10.9, do_s: 15.8, besedilo: "Celovška cesta, mislim da 150, v Ljubljani, v Šiški." },
    { id: "s5", govorec: "dispecer", od_s: 16.3, do_s: 17.6, besedilo: "Ali oče diha?" },
    { id: "s6", govorec: "klicatelj", od_s: 18.0, do_s: 22.7, besedilo: "Ja, diha, ampak zelo težko. In iz glave mu teče kri." },
    { id: "s7", govorec: "dispecer", od_s: 23.2, do_s: 24.6, besedilo: "Koliko je star vaš oče?" },
    { id: "s8", govorec: "klicatelj", od_s: 25.0, do_s: 31.9, besedilo: "Dvainšestdeset. Oprostite, ni 150, je 105! Rumena hiša za bencinsko črpalko." },
    { id: "s9", govorec: "dispecer", od_s: 32.5, do_s: 34.4, besedilo: "Ali je še kdo poškodovan?" },
    { id: "s10", govorec: "klicatelj", od_s: 34.9, do_s: 37.6, besedilo: "Ne, samo oče. Sosed je tukaj z mano." },
    { id: "s11", govorec: "dispecer", od_s: 38.1, do_s: 40.0, besedilo: "Na kateri številki ste dosegljivi?" },
    { id: "s12", govorec: "klicatelj", od_s: 40.4, do_s: 43.0, besedilo: "Na tej, 041 555 238." },
    { id: "s13", govorec: "dispecer", od_s: 43.6, do_s: 48.5, besedilo: "Ekipa je obveščena. Očeta ne premikajte, rano pritisnite s čisto krpo." },
  ],
  ai: {
    s2: {
      posodobitve: [
        { polje: "vrsta_dogodka", vrednost: "padec", zaupanje: 0.93, dokazi: [{ segment_id: "s2", citat: "padel z lestve" }] },
        { polje: "opis", vrednost: "Oče klicatelja padel z lestve, leži na tleh", zaupanje: 0.84, dokazi: [{ segment_id: "s2", citat: "moj oče je padel z lestve, leži na tleh" }] },
        { polje: "stanje_pacienta", vrednost: "ne odziva se", zaupanje: 0.86, dokazi: [{ segment_id: "s2", citat: "se ne odziva" }] },
        { polje: "stevilo_poskodovanih", vrednost: 1, zaupanje: 0.42, dokazi: [{ segment_id: "s2", citat: "moj oče je padel" }] },
      ],
      vprasanja_za_klicatelja: ["Kje točno ste – ulica, hišna številka, kraj?", "Ali pacient diha?"],
      zaznani_dogodki: [],
    },
    s4: {
      posodobitve: [
        { polje: "naslov", vrednost: "Celovška cesta 150, Ljubljana", zaupanje: 0.61, dokazi: [{ segment_id: "s4", citat: "Celovška cesta, mislim da 150, v Ljubljani" }] },
        { polje: "lokacija", vrednost: { lat: 46.07405, lon: 14.48565 }, zaupanje: 0.6, dokazi: [{ segment_id: "s4", citat: "Celovška cesta, mislim da 150" }] },
        { polje: "predlagana_enota", vrednost: "RV-2", zaupanje: 0.7, dokazi: [{ segment_id: "s4", citat: "Celovška cesta, mislim da 150, v Ljubljani" }] },
      ],
      vprasanja_za_klicatelja: ["Ali pacient diha?", "Ali je hišna številka 150 zanesljiva?"],
      zaznani_dogodki: [],
    },
    s6: {
      posodobitve: [
        { polje: "stanje_pacienta", vrednost: "diha zelo težko, ne odziva se, krvavi iz glave", zaupanje: 0.89, dokazi: [{ segment_id: "s6", citat: "diha, ampak zelo težko" }, { segment_id: "s6", citat: "iz glave mu teče kri" }, { segment_id: "s2", citat: "se ne odziva" }] },
        { polje: "prioriteta", vrednost: 1, zaupanje: 0.81, dokazi: [{ segment_id: "s2", citat: "se ne odziva" }, { segment_id: "s6", citat: "diha, ampak zelo težko" }] },
        { polje: "potrebna_oprema", vrednost: ["nosila", "vratna opornica", "komplet za oskrbo ran"], zaupanje: 0.58, dokazi: [{ segment_id: "s2", citat: "padel z lestve" }, { segment_id: "s6", citat: "iz glave mu teče kri" }] },
      ],
      vprasanja_za_klicatelja: ["Koliko je pacient star?", "Ali je še kdo poškodovan?"],
      zaznani_dogodki: [],
    },
    s8: {
      posodobitve: [
        { polje: "opis", vrednost: "Moški, 62 let, padel z lestve, leži na tleh", zaupanje: 0.9, dokazi: [{ segment_id: "s8", citat: "Dvainšestdeset" }, { segment_id: "s2", citat: "padel z lestve, leži na tleh" }] },
        { polje: "naslov", vrednost: "Celovška cesta 105, Ljubljana", zaupanje: 0.76, dokazi: [{ segment_id: "s8", citat: "ni 150, je 105" }] },
        { polje: "lokacija", vrednost: { lat: 46.07138, lon: 14.4887 }, zaupanje: 0.74, dokazi: [{ segment_id: "s8", citat: "ni 150, je 105" }] },
        { polje: "orientacija", vrednost: "rumena hiša za bencinsko črpalko", zaupanje: 0.91, dokazi: [{ segment_id: "s8", citat: "Rumena hiša za bencinsko črpalko" }] },
      ],
      vprasanja_za_klicatelja: ["Ali je še kdo poškodovan?", "Na kateri številki ste dosegljivi?"],
      zaznani_dogodki: [],
    },
    s10: {
      posodobitve: [
        { polje: "stevilo_poskodovanih", vrednost: 1, zaupanje: 0.93, dokazi: [{ segment_id: "s10", citat: "Ne, samo oče" }] },
      ],
      vprasanja_za_klicatelja: ["Na kateri številki ste dosegljivi?"],
      zaznani_dogodki: [],
    },
    s12: {
      posodobitve: [
        { polje: "kontakt", vrednost: "041 555 238", zaupanje: 0.95, dokazi: [{ segment_id: "s12", citat: "041 555 238" }] },
      ],
      vprasanja_za_klicatelja: [],
      zaznani_dogodki: [],
    },
  },
};

/* Enote (scenarij: UKC, 4 vozila, 2 motorista, 1 helikopter).
 * Časi prihoda so tisto, kar frontend dobi od skritega usmerjevalnega servisa:
 * le čas, razdalja in oznaka kakovosti – brez imena ponudnika. */
window.AID_ENOTE = [
  { id: "RV-1", tip: "vozilo", ime: "Reševalno vozilo 1", stanje: "na_nalogi" },
  { id: "RV-2", tip: "vozilo", ime: "Reševalno vozilo 2", stanje: "prosto" },
  { id: "RV-3", tip: "vozilo", ime: "Reševalno vozilo 3", stanje: "prosto" },
  { id: "RV-4", tip: "vozilo", ime: "Reševalno vozilo 4", stanje: "okvara" },
  { id: "MOT-1", tip: "motorist", ime: "Motorist 1", stanje: "prosto" },
  { id: "MOT-2", tip: "motorist", ime: "Motorist 2", stanje: "na_nalogi" },
  { id: "HELI-1", tip: "helikopter", ime: "Helikopter", stanje: "prosto" },
];
window.AID_ETA = [
  { enota_id: "MOT-1", cas_s: 250, razdalja_m: 3200, kakovost: "natančno" },
  { enota_id: "RV-2", cas_s: 380, razdalja_m: 3700, kakovost: "natančno" },
  { enota_id: "RV-3", cas_s: 560, razdalja_m: 6100, kakovost: "brez prometa" },
  { enota_id: "HELI-1", cas_s: 660, razdalja_m: 3400, kakovost: "ocena" },
];
