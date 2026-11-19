# Hit Em High Fantasy

*Fantasy Manager NBA in de huisstijl van [Hit Em High, de Nederlandse NBA-podcast](https://vriendvandeshow.nl/hit-em-high-de-nederlandse-nba-podcast/word-vriend): roze, cyaan en dikke letters (Archivo Black en Manrope). Het basketbal-icoon is een eigen tekening in die kleuren, niet het officiële logo.*


Een web-app in Football Manager-stijl voor je ESPN Fantasy Basketball-league met **H2H-categorieën**: de live categoriestand met winkans per categorie, je opstelling per dag, de waarde van elke speler (z-scores, attributen 1-20), het NBA-schema voor streamers en gebundeld nieuws. Eén server bedient de hele league en elke deelnemer kiest zijn eigen team.

Het is de NBA-versie van [fantasy-manager](https://github.com/MOvermans34/fantasy-manager) (football) en gebruikt dezelfde opzet: Python-server, PWA, toegangscode en een Render-deploy.

## Schermen

| Scherm | Wat je ziet |
|---|---|
| **League** | Alle matchups van deze periode met de categoriestand (bijv. 5-3-1), de verwachte eindstand, de winkans en per categorie een gekleurd label. Ook de stand (W-V-G). Klik op een team om de app als dat team te bekijken. |
| **Overzicht** | De categoriestand tegen je tegenstander. Per categorie zie je: nu, verwacht aan het eind van de week, winkans en een label (veilig, swing, verloren). Verder het aantal wedstrijden dat jij en je tegenstander nog hebben, een **inbox** (blessures, starters zonder wedstrijd terwijl er een bankspeler speelt, swing-categorieën, streamers en waiver-tips, nieuws) en je opstelling van vandaag. |
| **Selectie** | Je selectie met kwaliteit (1-20), vorm (laatste wedstrijden), de wedstrijd van vandaag, het aantal wedstrijden deze en volgende matchup, de waarde en de stats per wedstrijd met een kleur per z-score. Er zijn drie weergaven: overzicht, attributen en waarde per periode (seizoen, L7, L15, L30, verwacht, vorig seizoen) met trend. |
| **Spelerprofiel** | Attributen per categorie, waarde per wedstrijd (grafiek), gemiddelden per periode, advanced stats (Basketball Reference), ESPN-outlook, de volledige wedstrijdlog, het komende schema met per wedstrijd het tempo, de DRtg en de matchupfactor per categorie, nieuws en een vergelijking met directe concurrenten. |
| **Wedstrijdcentrum** | De live categoriestand, de statregels van jouw spelers en die van je tegenstander vandaag, een live-ticker met statwijzigingen en de wedstrijden van vandaag. Tijdens wedstrijden ververst dit elke 30 seconden. |
| **Spelersmarkt** | Alle spelers met hun stats per wedstrijd in de gekozen periode, gekleurd per z-score, of met de advanced stats van Basketball Reference (USG%, TS%, PER, BPM, VORP…). Je kunt filteren op positie en *vrij/waivers*, sorteren op elke kolom en **categorieën punten**: de waarde rekent dan zonder die categorieën. |
| **Categorieën** | Een heatmap van alle teams per categorie: seizoenstotalen van ESPN en de projectie per wedstrijd. Je ziet je eigen sterke en zwakke categorieën met een punt-advies, en vrije spelers die het meest helpen in je zwakste categorieën. |
| **Schema** | Het NBA-schema van deze en de volgende matchup per team, met het aantal wedstrijden te gaan. Elke wedstrijd is gekleurd naar hoe gunstig de tegenstander is (tempo + wat die defensie weggeeft), en per team zie je tempo en DRtg. Per dag zie je hoeveel van je spelers er spelen t.o.v. je basisplaatsen. Op dagen met meer spelers dan plaatsen gaan wedstrijden verloren. |
| **Nieuws** | ESPN-nieuws en Rotowire-spelersupdates, te filteren op bron, je eigen team, de tegenstander en per speler. |

## Hoe de analytics werken

- **Waarde (z-scores):** per categorie vergelijken we een speler met de rosterbare spelers (ongeveer 13 per team). Percentages (FG%, FT%) wegen we naar het aantal pogingen: 60% op 15 pogingen telt zwaarder dan 60% op 3. Bij TO is minder beter. De waarde is de som van de z-scores. Gepunte categorieën tellen niet mee.
- **Verwacht per wedstrijd:** een mix van seizoen, laatste 15 dagen en de ESPN-projectie, gewogen naar het aantal gespeelde wedstrijden. Vroeg in het seizoen telt vorig seizoen mee als er nog niets anders is.
- **Attributen (1-20):** dezelfde z-scores, omgezet naar 1-20. Een 10-11 is een gemiddelde rosterbare speler en een 20 is elite in die categorie. Kwaliteit is gebaseerd op de totale waarde.
- **Matchup-projectie:** de huidige stand van ESPN plus de verwachte productie in de resterende wedstrijden van de matchup. Per dag kiezen we de beste opstelling uit de spelers die spelen, binnen jouw basisplaatsen. Spelers met status *out* tellen niet mee en *dag-tot-dag* telt voor 80%. Voor vandaag gebruiken we je echte opstelling en alleen de nog te spelen speeltijd.
- **Tegenstanderfactor (Basketball Reference):** per wedstrijd kijken we naar het tempo van beide teams en naar wat de tegenstander per wedstrijd weggeeft per categorie (punten, rebounds, assists, steals, blocks, driepunters, turnovers, velddoelpunten en vrije worpen), vergeleken met het league-gemiddelde. De afwijking wordt gehalveerd tegen ruis en begrensd op ±15%. Deze factor telt mee in de matchup-projectie.
- **Winkans:** per categorie met een normale benadering van de onzekerheid (die afneemt naarmate de week vordert), en voor het totaal met 3.000 simulaties van alle categorieën samen.

## Delen met je league

De server leest de league met **jouw** ESPN-cookies. Je vrienden hebben geen cookies nodig:

1. Ze openen het adres van de app en vullen de **toegangscode** in die jij ze geeft.
2. Ze kiezen hun team uit de lijst. Dat onthoudt de app op hun apparaat.
3. Op de telefoon installeren ze de app via *Delen → Zet op beginscherm* (iPhone) of *⋮ → App installeren* (Android).

Een directe link naar een team kan ook: `https://jouw-adres/?team=3`. Met de knop **Link** linksboven kopieer je die.

**Veiligheid:** je `espn_s2`-cookie geeft volledige toegang tot je ESPN-account. Die blijft alleen op de server en wordt nooit naar een browser gestuurd. De app doet alleen leesverzoeken. Zet altijd een `ACCESS_CODE`.

## Online zetten op Render

De ESPN-cookies zijn dezelfde als die van je football-app: ze horen bij je ESPN-account, niet bij een league.

1. Zoek je **league-ID** en **team-ID** op: open je league op fantasy.espn.com/basketball. Het adres bevat `leagueId=…` en, op de pagina van je team, `teamId=…`.
2. Kies in Render **New → Blueprint** en selecteer deze repo. Render leest `render.yaml` in.
3. Vul de gevraagde waarden in:

| Variabele | Waarde |
|---|---|
| `LEAGUE_ID` | het league-ID van je NBA-league |
| `TEAM_ID` | je eigen team-ID (het standaardteam in de app) |
| `ESPN_S2` | je espn_s2-cookie (dezelfde als bij de football-app) |
| `ESPN_SWID` | je SWID-cookie, inclusief `{ }` |
| `ACCESS_CODE` | een code die je met je league deelt |
| `DATABASE_URL` | optioneel, zie *Nieuws-ETL* hieronder. Laat leeg als je het niet nodig hebt. |

4. Klik op **Apply**. Daarna krijg je een adres als `https://fantasy-manager-nba-xxxx.onrender.com`.

Het gratis plan valt na 15 minuten zonder bezoek in slaap; het eerste bezoek daarna duurt ongeveer een minuut. Zolang iemand de app open heeft, blijft hij wakker. Met `starter` ($7 per maand) is hij altijd aan. De app gebruikt ongeveer 100 MB geheugen.

**Onderhoud:** krijg je "401", dan zijn de cookies verlopen. Zet dan nieuwe waarden in Render onder *Environment*. Voor een nieuw seizoen pas je `SEASON` aan: ESPN telt een seizoen naar het jaar waarin het eindigt, dus 2027-28 is `2028`.

## Lokaal

Je hebt Python 3.9 of nieuwer nodig. Zonder database zijn er geen extra pakketten nodig.

```bash
cp config.example.json config.json   # vul league_id, team_id en de cookies in
python3 server.py
```

| Optie | Wat het doet |
|---|---|
| `--demo` | demoleague van 10 teams met openbare ESPN-data, geen cookies nodig |
| `--period 40` | een andere speeldag (scoring period) |
| `--lan` | ook bereikbaar op je telefoon via hetzelfde wifi |
| `--port 8080` | een andere poort |
| `--no-open` | de browser niet automatisch openen |

| Data | Interval |
|---|---|
| Live stats en categoriestand | 30 s tijdens wedstrijden, anders 5 min |
| Nieuws | 5 min |
| League, selecties en spelerspool | 15 min |

## Nieuws-ETL (Postgres + Polars)

Dit werkt hetzelfde als in de football-app. Met `DATABASE_URL` gaat het nieuws via extract (ESPN + Rotowire), transform (Polars) en load (Postgres, idempotent). Zonder `DATABASE_URL` gebruikt de app een lokaal JSON-bestand. Gebruik een **eigen** database, niet die van de football-app: anders komt het nieuws van beide sporten in dezelfde tabellen terecht. Render geeft maar één gratis database per account, dus een gratis Neon-database is handig.

## Databronnen

| Bron | Wat |
|---|---|
| **ESPN Fantasy API** (onofficieel) | league, categorieën, selecties, categoriestand, stats per wedstrijd en per periode, projecties, blessures, eigendom-% |
| **ESPN NBA-scoreboard** | wedstrijdstatus, kwart en klok |
| **Basketball Reference** | advanced stats per speler (PER, TS%, USG%, AST%, REB%, STL%, BLK%, TOV%, BPM, VORP, WS/48) en per team tempo, ORtg/DRtg en opponent per game |
| **Nieuws** | ESPN-nieuws, Rotowire-updates via ESPN en de Rotowire-RSS-feed |

**Over Basketball Reference:** de app haalt per verversing maar twee pagina's op (`/leagues/NBA_{jaar}_advanced.html` en `/leagues/NBA_{jaar}.html`), met 6 seconden ertussen en hooguit eens per 12 uur. Het resultaat wordt op schijf bewaard. Dat valt ruim binnen de regels van de site (robots.txt vraagt 3 s tussen verzoeken, Sports Reference blokkeert boven ongeveer 20 verzoeken per minuut). Gebruik het voor je eigen league en niet voor publieke of commerciële doeleinden; zie de gebruiksvoorwaarden van Sports Reference. Sommige cloudservers (zoals die van Render) worden door Basketball Reference geblokkeerd. Lukt het direct ophalen niet, dan valt de app terug op de snapshot in deze repo (`snapshots/bbref_<seizoen>.json`), eerst via GitHub en anders de versie in het Docker-image. Onder de advanced stats staat welke bron is gebruikt en hoe oud die is. De GitHub Action *Basketball Reference snapshot* ververst die snapshot elke dag om 09:17 UTC en commit hem alleen als er iets veranderd is. Starten kan ook handmatig via het tabblad Actions. Lukt het de Action niet, dan maak je de snapshot lokaal met `python -m ffd.bbref` en commit je hem. Uitzetten kan met `BBREF=0`. De app werkt dan gewoon zonder advanced stats en tegenstanderfactoren. Spelers worden op naam gekoppeld; wie dit seizoen nog niet heeft gespeeld, heeft (nog) geen advanced stats.

De ESPN-API is onofficieel. Als ESPN daar iets aan verandert, kan een deel tijdelijk niet werken. stats.nba.com blokkeert verzoeken van cloudservers, daarom komen de basisstats van ESPN.
