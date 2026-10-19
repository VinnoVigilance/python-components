"""Character TF-IDF with a balanced linear SVM for each active label."""
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

DESCRIPTION = 'Character TF-IDF within word boundaries (3,5) + balanced Linear SVM, C=1'


def build(seed=42):
    return Pipeline([
        ('tfidf', TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 5),
                                 min_df=2, max_df=0.95, max_features=100000,
                                 sublinear_tf=True)),
        ('classifier', OneVsRestClassifier(
            LinearSVC(C=1.0, class_weight='balanced', max_iter=10000,
                      random_state=seed), n_jobs=1)),
    ])
