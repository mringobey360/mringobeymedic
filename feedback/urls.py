from django.urls import path
from . import views
app_name='feedback'
urlpatterns=[
 path('feedback/',views.feedback_center,name='center'),
 path('feedback/submit/',views.submit_feedback,name='submit'),
 path('feedback/inbox/',views.feedback_inbox,name='inbox'),
 path('feedback/reply/<int:pk>/',views.feedback_reply,name='reply'),
 path('feedback/support/',views.support_center,name='support'),
 path('feedback/support/inbox/',views.support_inbox,name='support_inbox'),
 path('feedback/support/create/',views.support_create,name='support_create'),
 path('feedback/support/<int:pk>/',views.support_detail,name='support_detail'),
 path('feedback/support/<int:pk>/message/',views.support_message,name='support_message'),
 path('feedback/support/<int:pk>/update/',views.support_update,name='support_update'),
]
