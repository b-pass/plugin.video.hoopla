# plugin.video.hoopla

An unofficial Kodi addon for [hoopla digital](https://www.hoopladigital.com), the free streaming service
offered by public libraries. Borrow and watch hoopla's movies, TV and BingePasses on your TV with your
library card.

![](resources/fanart.jpg?raw=true)

## Features

- **Borrowed:** your current loans with due dates, plus your borrowing history.
- **Browse Movies and Television:** search, Featured, Popular, Recently added, collections and genres.
- **TV:** seasons and episodes, with each episode's own artwork.
- **BingePasses:** browse a pass's shows and movies, then borrow the pass once to watch all of it.
- **Borrow as you play:** pick something you haven't borrowed and the addon asks first, showing how many
  borrows you have left this month and how long the loan lasts.
- **Context menu:**
  - *Series*: other seasons or films in the same series
  - *More like this*
  - *Rate...*: rate a title, or an episode's season
  - *Return*: return a title early

## Requirements

- Kodi 20 (Nexus) or later
- A hoopla account from your public library
- Widevine DRM, installed through the **InputStream Helper** addon. Kodi installs it along with this
  addon, and the addon offers to set up Widevine the first time you play something.

## Installation

1. Download this repository as a zip: **Code → Download ZIP**.
2. In Kodi, go to **Settings → Add-ons → Install from zip file** and pick the zip. You may first need
   to allow **Unknown sources** in Kodi's system settings.
3. Open the addon's settings and enter your hoopla email and password.

Kodi expects the folder inside the zip to be named `plugin.video.hoopla`. GitHub's zip names it
`plugin.video.hoopla-main`, so rename the folder and re-zip it if Kodi refuses the zip.

## Usage notes

- Borrowing uses one of your library's monthly borrows, just like on hoopla's website or apps. A
  BingePass costs one borrow for the whole pass.
- Only movies, TV and BingePasses with video are shown. Audiobooks, ebooks, comics and music aren't
  supported.
- If playback doesn't start, open the InputStream Helper addon and make sure Widevine is installed.
  Settings → Playback → *Play even if the Widevine check fails* is for devices that provide Widevine
  some other way.

## Development

The addon is split into three layers:
- `addon.py`: the Kodi UI.
- `dao.py`: the API-neutral contract, `LibraryDAO`.
- `hoopla_graphql.py`: the hoopla implementation.

The DAO layer is Kodi-free and tested against recorded API responses:

```
python3 -m unittest discover tests
```

## Disclaimer

This addon is **unofficial**. It is not affiliated with or authorized by hoopla digital, Midwest Tape, or
any library. The [LICENSE](LICENSE) (The Unlicense) covers this addon's code only. The hoopla name and
logo, and all titles, images and video shown through the addon, belong to their respective owners.
