// The window every Asgard app shares: sidebar on the left, the current page on the right,
// toasts in the corner. Every colour comes from `theme`, so apps and people restyle it there.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    visible: true
    width: 1180
    height: 760
    minimumWidth: 900
    minimumHeight: 560
    title: navigation.currentTitle + " - " + shell.title
    color: theme.background
    font.family: theme.fontFamily
    font.pixelSize: theme.fontSize

    // The Basic style draws text fields, combo boxes, spin boxes and dialogs from the palette.
    palette.window: theme.background
    palette.windowText: theme.text
    palette.base: theme.surface
    palette.alternateBase: theme.surfaceHigh
    palette.text: theme.text
    palette.button: theme.surfaceHigh
    palette.buttonText: theme.text
    palette.brightText: theme.accentText
    palette.highlight: theme.accent
    palette.highlightedText: theme.accentText
    palette.placeholderText: theme.textDim
    palette.light: theme.surface
    palette.midlight: theme.borderSoft
    palette.mid: theme.border
    palette.dark: theme.border
    palette.shadow: theme.border
    palette.toolTipBase: theme.surfaceHigh
    palette.toolTipText: theme.text

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

    // Asgard's home window only: a yellow warning banner on top and bottom (Settings > Banner).
    WarningBanner {
        id: topBanner
        objectName: "bannerTop"
        visible: prefs.bannerShown
        Layout.fillWidth: true
        text: prefs.bannerText
        animate: prefs.bannerAnimate
        speed: prefs.bannerSpeed
        fontFamily: prefs.bannerFont
        colors: prefs.bannerColors
        stripeAtBottom: true
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        spacing: 0

        Sidebar {
            Layout.fillHeight: true
            Layout.preferredWidth: theme.sidebarWidth
        }


        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            Loader {
                id: pageLoader
                objectName: "pageLoader"
                anchors.fill: parent
                source: navigation.currentSource
                onLoaded: {
                    // Pages that declare `property var bridge` get their app's backend.
                    if (item && item.bridge !== undefined)
                        item.bridge = navigation.bridgeFor(navigation.currentApp)
                }
                onStatusChanged: {
                    if (status === Loader.Error)
                        shell.toast("Couldn't open " + navigation.currentTitle + ". Details are in the log.", "error")
                }
            }

            EmptyState {
                anchors.centerIn: parent
                visible: pageLoader.status === Loader.Error
                title: "This page couldn't open"
                detail: "Its QML has a problem. The log in Asgard's logs folder says which line."
            }
        }
    }

    WarningBanner {
        id: bottomBanner
        objectName: "bannerBottom"
        visible: prefs.bannerShown
        Layout.fillWidth: true
        text: prefs.bannerText
        animate: prefs.bannerAnimate
        speed: prefs.bannerSpeed
        fontFamily: prefs.bannerFont
        colors: prefs.bannerColors
        stripeAtBottom: false
        reverse: true
    }
    }

    onActiveChanged: {
        if (active && pageLoader.item && typeof pageLoader.item.refresh === "function")
            pageLoader.item.refresh()
    }

    // For tests and diagnostics: read the page without Python holding a reference to it.
    function pageReady() { return pageLoader.status === Loader.Ready && pageLoader.item !== null }
    function pageValue(name) { return pageLoader.item ? pageLoader.item[name] : undefined }
    function bannerInfo(which) {
        var b = which === "top" ? topBanner : bottomBanner
        return { "visible": b.visible, "text": b.text, "animate": b.animate }
    }
    function pageCall(name) {
        if (!pageLoader.item || typeof pageLoader.item[name] !== "function")
            return false
        pageLoader.item[name]()
        return true
    }

    ToastHost {
        id: toastHost
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 20
        anchors.bottomMargin: 20 + (bottomBanner.visible ? bottomBanner.height : 0)
    }

    Connections {
        target: shell
        function onToastRequested(text, kind) { toastHost.show(text, kind) }
    }

    Shortcut {
        sequences: ["Alt+Left"]
        onActivated: navigation.back()
    }
    Shortcut {
        sequences: [StandardKey.Refresh]
        onActivated: {
            if (pageLoader.item && typeof pageLoader.item.reload === "function")
                pageLoader.item.reload()
        }
    }
}
