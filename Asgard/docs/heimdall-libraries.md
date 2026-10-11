# Publishing Heimdall libraries

A guide for whoever looks after a list that templates pick from, such as a firewall's address groups. How people use a library is in [Writing Heimdall templates](heimdall-templates.md).

## What a library is

A library is a named list of entries. Each has a name people pick (`web-servers`), an optional description, and the value that goes into the form (`10.20.0.0/24`). A template uses it with `{SRC:LIBRARY('ip_library_12'):'Source'}`: Heimdall asks for an entry, and fills in its value.

You publish a library as a JSON file. People import it; Heimdall keeps an encrypted copy on their computer. Nothing is fetched over the network.

## The file

<!-- example: library -->
```json
{
  "library": "ip_library_12",
  "description": "Data centre address groups",
  "version": "2026-10-01",
  "renders": "value",
  "entries": [
    {"name": "web-servers", "value": "10.20.0.0/24", "description": "DMZ web tier"},
    {"name": "db-servers", "value": ["10.30.0.5", "10.30.0.6"], "description": "Payroll DB"},
    {"name": "jump-hosts", "value": "10.40.0.9"}
  ]
}
```

| Key | Meaning |
| --- | --- |
| `library` | The name templates use: 1 to 64 letters, digits, `.`, `_` or `-`, starting with a letter or digit. Required |
| `description`, `version` | Text, shown in lists. `version` is yours to choose (a date works well) |
| `renders` | `"value"` (the default) or `"name"`. See below |
| `entries` | The list, up to 50,000. Required. An object of name to value also works: `{"web-servers": "10.20.0.0/24"}` |
| entry `name` | One line, unique within the library (case doesn't matter). Required |
| entry `value` | Text, or a list of text (filled in as the items joined with `, `). Required when `renders` is `value` |
| entry `description` | Text that helps people choose. Searches look at it |

### `renders: value` or `renders: name`

- **`value`** puts the entry's value in the form. Heimdall keeps the values (encrypted), shows only names and descriptions in lists and searches, and shows a value only as it goes into a form the person is about to review.
- **`name`** puts the entry's name in the form, for lists whose values people shouldn't have. Heimdall **drops the values when it imports**, so they are never on the computer at all. If users shouldn't be able to read the values, this is the only mode that delivers it (see the caveat below).

## Publishing and updating

Keep the file where the people who need it can read it: a repository checkout or a share. Then:

```
asgard heimdall library import \\share\security\ip_library_12.json    first time, or to update
asgard heimdall library list                                          what's here, with versions
asgard heimdall library show ip_library_12 --search web               entries (names and descriptions)
asgard heimdall library search ip_library_12 web --json               the same search, for a program
asgard heimdall library remove ip_library_12
```

To update a library, publish a new file with a higher `version` and ask users to import it again: importing a library with the same name replaces it, and says how many entries it had. Heimdall doesn't check for updates by itself. The published file is plain text; users should delete their copy after importing, since Heimdall keeps its own.

If a file is wrong, import names what to fix: a missing `name` or `value` (`entry "web-servers" needs a "value": text, or a list of text`), two entries with the same name, a `library` name with a character that isn't allowed, or `renders` that isn't `value` or `name`.

## What the encryption is, and isn't

On Windows each library is encrypted with DPAPI for the signed-in user. That keeps it from other Windows accounts, from other computers, and from backups. **It does not hide it from the user it belongs to**: anything running as them can decrypt it. Heimdall never shows values in a list or a search, but a determined user can read their own copy. Where the values are secret from users themselves, publish with `"renders": "name"`, or keep the list where Heimdall can't reach it.

Off Windows there is no DPAPI, so libraries are refused unless `HEIMDALL_UNPROTECTED_LIBRARIES=1` says to store them unencrypted. That is for trying Heimdall out on a development machine; Windows ignores the setting and won't read an unencrypted copy.

## For AI clients

Ysildir's Heimdall tools (off by default) list libraries and search entries by name and description. They never return a value, and a plan that picks a library entry shows the entry's name. See [integration/ysildir.md](integration/ysildir.md).
