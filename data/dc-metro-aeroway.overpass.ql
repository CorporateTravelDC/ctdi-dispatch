[out:json][timeout:180];
(
  area["icao"="KBWI"];
  area["icao"="KDCA"];
  area["icao"="KIAD"];
  area["icao"="KHEF"];
  area["icao"="KJYO"];
  area["icao"="KFDK"];
)->.dca;
(
  nwr(area.dca)["aeroway"~"^(apron|terminal|gate)$"];
);
out tags geom;
