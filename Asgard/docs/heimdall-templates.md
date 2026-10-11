# Writing Heimdall templates

A guide for whoever writes the templates people fill security change requests from. It covers the fill-in-the-blanks language, with worked examples, every format and check, escaping, and the mistakes the parser reports. [Libraries](heimdall-libraries.md) are covered separately.

## What a template is

A template is a saved set of values for the security change form's fields, kept in `%LOCALAPPDATA%\Asgard\settings\heimdall-templates.json`. Filling one opens Edge, types the values in, and stops before **Submit**: the person reviews the form and clicks Submit themselves.

```
asgard heimdall fields                                    the form's fields (labels and kinds)
asgard heimdall template save "Monthly scan" --set "Short description=Monthly scan $today" --set "Category=Hardware"
asgard heimdall template list | show NAME | edit NAME | delete NAME
asgard heimdall fill -t "Monthly scan" --dry-run          what a fill would type; no browser
asgard heimdall fill -t "Monthly scan"                    open Edge and fill
```

`$today` and `$today_us` fill in the date themselves. Anything else that changes from one request to the next is a **variable**.

## Variables

A variable is written into a field's value as `{NAME:FORMAT(args):"Friendly name"}`. When the template is filled, Heimdall asks for each one. The arguments and the friendly name are optional: `{SOURCE_ADDRESS:TEXTBOX}` is a complete definition, and its friendly name is "Source address".

| Format | Written as | The person gives | Fills in |
| --- | --- | --- | --- |
| `TEXTBOX` | `{HOST:TEXTBOX('ip_v4'):'Host'}` | Typed text, checked by the listed checks | The text, exactly as typed |
| `DROPDOWN` | `{ENV:DROPDOWN('prod','test'):'Environment'}` | One of the listed options (case doesn't matter) | The option as you spelled it |
| `CHECKBOX` | `{URGENT:CHECKBOX('Emergency change'):'Urgent'}` | Checked or not; never required | `Yes` or `No` |
| `LIBRARY` | `{SRC:LIBRARY('ip_library_12'):'Source'}` | The name of an entry in that [library](heimdall-libraries.md) | The entry's value (or its name, for a library that keeps no values) |

**Checks** a `TEXTBOX` can list: `string` (anything non-empty, the default), `bool`, `int`, `float`, `ip_v4` and `ip_v6`. Listing several accepts a value that passes any of them: `TEXTBOX('ip_v4','ip_v6')`. `ip_v4` and `ip_v6` take one address, not a range such as `10.0.0.0/24`.

**Names** are letters, digits and `_`, not starting with a digit, and case doesn't matter (`{src}` is `{SRC}`). A name means one thing in a template: define it once, then write `{NAME}` on its own wherever the value is used again.

Values are inserted exactly as typed. Only the template's own text is searched for `$` and braces, so a value such as `$5` or `{x}` is never misread.

### A worked example: firewall access

<!-- example: template -->
```
We need access to {SYSTEM_NAME:TEXTBOX('string'):'System Name'} from {SRC_ADDR:LIBRARY('ip_library_12'):'Source'} to {DEST_ADDR:LIBRARY('ip_library_12'):'Destination'}. Urgent: {URGENT:CHECKBOX('Emergency change'):'Urgent'} ($today)
```

Save it as a template's `Short description`, and repeat a variable in another field with its bare name:

```
asgard heimdall template save "Firewall access" --set "Short description=We need access to {SYSTEM_NAME:TEXTBOX('string'):'System Name'} from {SRC_ADDR:LIBRARY('ip_library_12'):'Source'} to {DEST_ADDR:LIBRARY('ip_library_12'):'Destination'}. Urgent: {URGENT:CHECKBOX('Emergency change'):'Urgent'} ($today)" --set "Requested for={DEST_ADDR}"
asgard heimdall template check "Firewall access"       is it usable here, and what will it ask for?
asgard heimdall template variables "Firewall access"   the variables, with their checks and options (--json for a program)
```

Filling it, with the library `ip_library_12` imported:

```
asgard heimdall fill -t "Firewall access" --dry-run --var SYSTEM_NAME=Payroll --var SRC_ADDR=web-servers --var DEST_ADDR=jump-hosts --var URGENT=true
```

shows `We need access to Payroll from 10.20.0.0/24 to 10.40.0.9. Urgent: Yes (` and the day's date `)`. With no `--var` at a terminal, `fill` asks for each value in turn, checks it as you type, and asks again after a wrong answer (end a library answer with `?` to list matching entries). In a script, or with `--json`, it never asks: it stops and names what's missing.

Three ways to give the values, which combine (`--var` overrides the file):

| How | Use it for |
| --- | --- |
| `--var NAME=VALUE`, repeated | A few values |
| `--vars FILE.json` | Long or repeated fills: one JSON object, `{"SYSTEM_NAME": "Payroll", "URGENT": true}` |
| The prompts | A person at a terminal |

## Where variables can go

Text, select and reference fields can hold variables. A checkbox field takes only `true` or `false` (`"I confirm" is a checkbox: use true or false`); put a `CHECKBOX` variable in the text of another field instead, where it fills in `Yes` or `No`.

## Escaping

| To write | Type |
| --- | --- |
| a literal `{` or `}` | `{{` or `}}` |
| a literal dollar sign | `$$` |
| the date placeholder | `${today}` or `$today`; it isn't a variable |

In PowerShell, `$today` inside double quotes is PowerShell's own variable and comes out empty. Write it with a backtick, `` `$today ``, or use Command Prompt for `--set` values that hold it.

## Mistakes the parser reports

A template with a broken definition is refused when you save it, and again if a fill reads it, with what to change. The ones people meet:

| You wrote | Heimdall says |
| --- | --- |
| `{HOST:TEXTBOX('ipv4')}` | `'ipv4' isn't a check Heimdall knows (ip_v4, ip_v6, string, bool, int, float)` |
| `{HOST:BUTTON}` | `the format must be one of CHECKBOX, TEXTBOX, DROPDOWN, LIBRARY` |
| `{HOST:LIBRARY:('x')}` | `write LIBRARY('...') with no colon before the bracket` |
| `{HOST:LIBRARY}` | `LIBRARY needs one library name, like LIBRARY('ip_library_12')` |
| `{HOST:DROPDOWN}` | `DROPDOWN needs the values to offer, like DROPDOWN('prod', 'test')` |
| `{HOST:DROPDOWN('a','a')}` | `DROPDOWN lists the same value twice` |
| `{HOST}` with no definition anywhere | `{HOST} is used but never defined. Define it once, like {HOST:TEXTBOX:"Friendly name"}` |
| `{A:TEXTBOX}` and `{A:DROPDOWN('x')}` | `A is defined twice, differently ... Define it once and write {A} where it's used again` |
| `{HOST:TEXTBOX('string'}` | `expected , or ) in the list` |
| `{1ST:TEXTBOX}` | `a variable needs a NAME of letters, digits and _ first` |
| `cost }` | `a lone } at character 6: write }} for a literal brace` |
| `${today` | `a ${ at character 1 is never closed: write ${today}, or $$ for a literal dollar sign` |

At fill time: `Give a value for Source (SRC_ADDR), Destination (DEST_ADDR).` names every variable still missing; `Host must be an IPv4 address; 'y' isn't.` says what a check wanted; `"web" isn't in the library ip_library_12. Did you mean: web-servers?` names close entries; and `the library ip_library_12 isn't on this computer; import it with 'asgard heimdall library import FILE'` means the [library](heimdall-libraries.md) isn't here yet.

## Template files and versions

Templates made before variables existed (file version 1) are read with their braces as plain text, so nothing changes meaning. The first change you make writes version 2; an older Heimdall then refuses the file, says so, and overwrites nothing.

## For AI clients

With Ysildir's Heimdall tools switched on (they're off by default, one switch each), an AI client can list templates and what they ask for, search libraries by name, and show a plan of what a fill would type. It can't fill a form or click Submit, and it never sees a library's values. See [integration/ysildir.md](integration/ysildir.md).
