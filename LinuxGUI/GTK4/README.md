# Ventoy GTK4

Interfaccia grafica moderna (Python + GTK4/libadwaita) per [Ventoy](https://www.ventoy.net) su Linux.
Non sostituisce Ventoy: raccoglie le opzioni e lancia lo script ufficiale `Ventoy2Disk.sh` del pacchetto, con i privilegi di root ottenuti tramite `pkexec`.

![Schermata](screenshot.png)

## Funzioni

- Elenco delle chiavette USB e dei dischi rimovibili, aggiornato da solo quando ne colleghi o scolleghi una (i dischi interni si mostrano solo su richiesta)
- Riconosce le chiavette che hanno già Ventoy e ne legge versione, stile di partizione e Secure Boot
- Installazione, reinstallazione forzata, aggiornamento e installazione non distruttiva
- Opzioni: GPT/MBR, Secure Boot, etichetta della partizione, spazio riservato a fine disco
- Interruttori per Windows 11 (salto del controllo requisiti, account locale) scritti in `ventoy/ventoy.json` senza toccare il resto del file
- Montaggio e apertura della partizione dati per copiarci le ISO
- Registro dello script in tempo reale e conferma esplicita prima di ogni operazione distruttiva

## Requisiti

- Il [pacchetto Linux di Ventoy](https://github.com/ventoy/Ventoy/releases) (`ventoy-x.y.z-linux.tar.gz`)
- Python 3 con PyGObject, GTK 4 e libadwaita ≥ 1.5
- `pkexec` (polkit), `lsblk`, `udisksctl`

Su Ubuntu/Debian:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 policykit-1 udisks2
```

## Uso

Nei pacchetti Linux generati da questo fork `VentoyGTK4.py` è già incluso (lo aggiunge `INSTALL/ventoy_pack.sh`). Con un pacchetto ufficiale, copialo nella cartella del pacchetto accanto a `Ventoy2Disk.sh`:

```bash
cp VentoyGTK4.py ~/ventoy-1.1.18/
cd ~/ventoy-1.1.18
./VentoyGTK4.py
```

Per aggiungere "Ventoy" al menu delle applicazioni:

```bash
./VentoyGTK4.py --install-desktop
```

## Note

- Solo la chiamata a `Ventoy2Disk.sh` gira come root; l'interfaccia resta in esecuzione come utente normale.
- Come per la GUI ufficiale, lo script scrive `log.txt` e la prima volta scompatta gli strumenti in `tool/`: quei file risulteranno di proprietà di root.
- Le conferme interattive dello script ricevono automaticamente "y", perché la conferma viene già chiesta dall'interfaccia.

## Licenza

GPL-3.0, come il resto di Ventoy. Ventoy è © longpanda; questa interfaccia è un contributo indipendente, non affiliato al progetto ufficiale.
