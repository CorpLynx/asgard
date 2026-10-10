// A yellow warning strip with scrolling text that pulses red, and a black-and-yellow hazard edge.
// Click it, or Tab to it and press Space, to pause the scrolling. With animate off it holds still.
// Colours come from prefs.bannerColors (asgard/ui/prefs.py), chosen so every point of the pulse
// keeps 4.5:1 contrast on the yellow.
import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    property string text: ""
    property bool animate: true
    property real speed: 80                 // pixels per second
    property string fontFamily: theme.monoFamily
    property bool stripeAtBottom: true      // the hazard edge faces the page
    property bool reverse: false            // scroll left to right instead
    property var colors: ({ "background": "#FFD60A", "stripe": "#1A1A1A", "dim": "#6A0000",
                            "bright": "#BE0000", "still": "#9A0000", "pulseMs": 1600 })
    property bool paused: false

    readonly property real stripeHeight: 5
    readonly property real gap: 96
    property color pulseInk: colors.dim
    readonly property color ink: animate ? pulseInk : colors.still

    implicitHeight: 34
    color: colors.background
    clip: true
    activeFocusOnTab: animate

    Accessible.role: Accessible.StaticText
    Accessible.name: "Banner: " + text
    Accessible.description: animate ? (paused ? "Paused. Press Space to scroll." : "Scrolling. Press Space to pause.") : ""
    Keys.onSpacePressed: if (animate) paused = !paused

    SequentialAnimation on pulseInk {
        running: root.animate && root.visible
        loops: Animation.Infinite
        ColorAnimation { from: root.colors.dim; to: root.colors.bright; duration: root.colors.pulseMs / 2; easing.type: Easing.InOutSine }
        ColorAnimation { from: root.colors.bright; to: root.colors.dim; duration: root.colors.pulseMs / 2; easing.type: Easing.InOutSine }
    }

    // Hazard edge: black diagonals on the yellow.
    Item {
        id: stripe
        width: parent.width
        height: root.stripeHeight
        y: root.stripeAtBottom ? parent.height - height : 0
        clip: true
        Repeater {
            model: Math.ceil(stripe.width / 14) + 2
            delegate: Rectangle {
                required property int index
                x: index * 14 - 10
                y: -6
                width: 7
                height: 18
                rotation: 40
                color: root.colors.stripe
            }
        }
        Accessible.ignored: true
    }

    Item {
        id: track
        anchors.fill: parent
        anchors.topMargin: root.stripeAtBottom ? 0 : root.stripeHeight
        anchors.bottomMargin: root.stripeAtBottom ? root.stripeHeight : 0
        clip: true

        TextMetrics {
            id: metrics
            font: still.font
            text: root.text
        }
        readonly property real unit: Math.max(1, metrics.advanceWidth + root.gap)

        Row {
            id: strip
            visible: root.animate
            y: (track.height - height) / 2
            Repeater {
                model: root.animate ? Math.ceil(track.width / track.unit) + 1 : 0
                delegate: Text {
                    width: track.unit
                    text: root.text
                    color: root.ink
                    font: still.font
                    Accessible.ignored: true
                }
            }

            NumberAnimation on x {
                id: scroll
                from: root.reverse ? -track.unit : 0
                to: root.reverse ? 0 : -track.unit
                duration: Math.max(1, track.unit / Math.max(1, root.speed) * 1000)
                loops: Animation.Infinite
                running: root.animate && root.visible
                paused: root.paused
            }
        }

        // Restart so a new text length or speed takes effect without a jump.
        onUnitChanged: if (scroll.running) scroll.restart()
        Connections {
            target: root
            function onSpeedChanged() { if (scroll.running) scroll.restart() }
        }

        Text {
            id: still
            visible: !root.animate
            anchors.centerIn: parent
            width: Math.min(implicitWidth, track.width - 24)
            elide: Text.ElideRight
            text: root.text
            color: root.ink
            font.family: root.fontFamily
            font.pixelSize: 15
            font.bold: true
            font.letterSpacing: 2
            Accessible.ignored: true
        }
    }

    Rectangle {
        anchors.fill: parent
        color: "transparent"
        border.width: root.activeFocus ? 2 : 0
        border.color: root.colors.stripe
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        enabled: root.animate
        hoverEnabled: true
        cursorShape: root.animate ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: root.paused = !root.paused
    }

    ToolTip.visible: mouse.containsMouse && root.animate
    ToolTip.delay: 800
    ToolTip.text: root.paused ? "Click to scroll" : "Click to pause"
}
