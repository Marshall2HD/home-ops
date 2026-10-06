/**
 * Adds Claude models released after the SillyTavern build to the Claude source
 * model picker. The picker is a static list in public/index.html, so a new model
 * id cannot be selected from the UI until it is added to that list.
 *
 * Third-party extensions activate after SillyTavern has applied the saved
 * settings. At that point an extra model is not yet in the picker, so
 * SillyTavern falls back to its default Claude model and saves it. The
 * extension remembers the chosen model in its own settings and restores it
 * once its option exists.
 */
import { saveSettingsDebounced, settings } from '../../../../script.js';
import { eventSource, event_types } from '../../../events.js';
import { extension_settings } from '../../../extensions.js';
import { oai_settings } from '../../../openai.js';

const SELECT_ID = 'model_claude_select';
const SETTINGS_KEY = 'claudeModelExtras';

// Insert each id directly after the model it supersedes, matching the picker's own order.
const EXTRA_MODELS = [
    { id: 'claude-opus-5-5', after: 'claude-opus-5' },
];

function isExtraModel(id) {
    return EXTRA_MODELS.some(model => model.id === id);
}

function findOption(select, value) {
    for (const option of select.options) {
        if (option.value === value) {
            return option;
        }
    }

    return null;
}

function injectExtraModels() {
    const select = document.getElementById(SELECT_ID);

    if (!select) {
        return;
    }

    const group = select.querySelector('optgroup') || select;

    for (const model of EXTRA_MODELS) {
        if (findOption(select, model.id)) {
            continue;
        }

        const option = document.createElement('option');
        option.value = model.id;
        option.textContent = model.id;

        const anchor = model.after ? findOption(select, model.after) : null;

        if (anchor && anchor.parentElement === group) {
            anchor.after(option);
        } else {
            group.appendChild(option);
        }

        console.debug('claude-model-extras: added ' + model.id + ' to #' + SELECT_ID);
    }
}

function rememberModel() {
    const model = String($('#' + SELECT_ID).val() || '');

    if (model && extension_settings[SETTINGS_KEY].model !== model) {
        extension_settings[SETTINGS_KEY].model = model;
        saveSettingsDebounced();
    }
}

function restoreRememberedModel() {
    // The raw loaded settings still hold the extra model unless a save already replaced them.
    const loadedModel = settings?.oai_settings?.claude_model;
    const model = extension_settings[SETTINGS_KEY].model ?? (isExtraModel(loadedModel) ? loadedModel : null);

    if (!isExtraModel(model) || oai_settings.claude_model === model) {
        return;
    }

    console.info('claude-model-extras: restoring ' + model + ' over load-time fallback ' + oai_settings.claude_model);
    $('#' + SELECT_ID).val(model).trigger('change');
    saveSettingsDebounced();
}

extension_settings[SETTINGS_KEY] ??= {};
injectExtraModels();
restoreRememberedModel();
rememberModel();
$('#' + SELECT_ID).on('change', rememberModel);
eventSource.on(event_types.APP_READY, injectExtraModels);
eventSource.on(event_types.SETTINGS_LOADED, injectExtraModels);
