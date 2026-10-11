// One of an app's own settings, drawn from what its backend declared: the label, the control its kind
// needs, and the help under it. Saving goes straight to the app, which validates it; if the app refuses,
// the message appears here and the control goes back to the stored value.
//
// Kinds: text, number, bool, choice, list, time (see asgard/ui/registry.py).
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    property string app: ""
    property var field: ({})
    property var value: undefined
    property string problem: ""
    readonly property string kind: field.kind || "text"
    readonly property bool isNumber: kind === "number"
    readonly property real step: field.step !== undefined ? field.step : 1

    spacing: 3

    function save(v) {
        problem = prefs.setAppSetting(root.app, root.field.key, v)
        if (problem === "")
            shell.toast(root.field.label + " saved.", "info")
    }
    function lines(v) {
        return (v === undefined || v === null || v === "") ? "" : (Array.isArray(v) ? v.join("\\n") : String(v))
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 12

        ColumnLayout {
            spacing: 0
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignTop
            Text {
                text: root.field.label
                color: theme.text
                font.family: theme.fontFamily
                font.pixelSize: theme.fontSize
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Text {
                visible: !!root.field.help
                text: root.field.help || ""
                color: theme.textMuted
                font.family: theme.fontFamily
                font.pixelSize: theme.fontSize - 2
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Text {
                visible: root.problem !== ""
                text: root.problem
                color: theme.error
                font.family: theme.fontFamily
                font.pixelSize: theme.fontSize - 2
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        // ---- bool
        Switch {
            visible: root.kind === "bool"
            checked: root.value === true
            Layout.alignment: Qt.AlignTop
            Accessible.name: root.field.label
            onToggled: root.save(checked)
        }

        // ---- choice
        ComboBox {
            visible: root.kind === "choice"
            Layout.preferredWidth: 260
            Layout.alignment: Qt.AlignTop
            model: root.field.options || []
            textRole: "label"
            valueRole: "value"
            font.family: theme.fontFamily
            font.pixelSize: theme.fontSize
            currentIndex: {
                var options = root.field.options || []
                for (var i = 0; i < options.length; i++)
                    if (options[i].value === root.value)
                        return i
                return -1
            }
            Accessible.name: root.field.label
            onActivated: root.save(currentValue)
        }

        // ---- number
        RowLayout {
            visible: root.isNumber
            spacing: 6
            Layout.alignment: Qt.AlignTop
            SpinBox {
                id: spin
                // SpinBox is whole numbers, so a fractional step (0.1) is held as tenths and scaled back.
                readonly property int factor: root.step < 1 ? Math.round(1 / root.step) : 1
                from: Math.round((root.field.min !== undefined ? root.field.min : 0) * factor)
                to: Math.round((root.field.max !== undefined ? root.field.max : 100000) * factor)
                stepSize: Math.max(1, Math.round(root.step * factor))
                value: Math.round((Number(root.value) || 0) * factor)
                editable: true
                font.family: theme.fontFamily
                font.pixelSize: theme.fontSize
                Accessible.name: root.field.label
                textFromValue: function (v) { return factor === 1 ? String(v) : (v / factor).toFixed(1) }
                valueFromText: function (text) { return Math.round(parseFloat(text) * factor) }
                onValueModified: root.save(factor === 1 ? value : value / factor)
            }
            Text {
                visible: !!root.field.unit
                text: root.field.unit || ""
                color: theme.textMuted
                font.family: theme.fontFamily
                font.pixelSize: theme.fontSize - 1
                Layout.alignment: Qt.AlignVCenter
            }
        }

        // ---- text and time
        TextField {
            id: line
            visible: root.kind === "text" || root.kind === "time"
            Layout.preferredWidth: root.kind === "time" ? 110 : 280
            Layout.alignment: Qt.AlignTop
            text: root.value === undefined || root.value === null ? "" : String(root.value)
            placeholderText: root.field.placeholder || (root.kind === "time" ? "07:30" : "")
            font.family: theme.fontFamily
            font.pixelSize: theme.fontSize
            Accessible.name: root.field.label
            onAccepted: root.save(text)
            onActiveFocusChanged: {
                if (!activeFocus && text !== String(root.value === undefined || root.value === null ? "" : root.value))
                    root.save(text)
            }
        }

        // ---- list
        ColumnLayout {
            visible: root.kind === "list"
            spacing: 4
            Layout.preferredWidth: 280
            Layout.alignment: Qt.AlignTop
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: Math.min(120, Math.max(60, box.implicitHeight + 16))
                radius: theme.radiusSmall
                color: theme.surfaceHigh
                border.width: box.activeFocus ? 2 : 1
                border.color: box.activeFocus ? theme.focus : theme.borderSoft
                ScrollView {
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true
                    TextArea {
                        id: box
                        text: root.lines(root.value)
                        color: theme.text
                        selectionColor: theme.accent
                        selectedTextColor: theme.accentText
                        font.family: theme.monoFamily
                        font.pixelSize: theme.fontSize - 1
                        wrapMode: TextArea.NoWrap
                        background: Item {}
                        Accessible.name: root.field.label + ", one per line"
                    }
                }
            }
            AppButton {
                text: "Save"
                visible: box.text !== root.lines(root.value)
                onClicked: root.save(box.text.split("\\n"))
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.topMargin: 4
        implicitHeight: 1
        color: theme.borderSoft
        opacity: 0.6
    }
}
